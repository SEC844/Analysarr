"""Assistant de nettoyage : collecte des faits en quelques requêtes groupées
(jamais une requête par média), puis score pur (services/cleanup_score.py).

Calcul à la volée plutôt qu'une table de scores précalculés : il dépend de
l'instant (jours sans lecture), des réglages et de l'activité des comptes,
qu'une table devrait recalculer à chaque changement. Mesure sur 10 000
médias, 80 000 lignes de visionnage et 10 000 torrents : environ 1 s en
chargeant des objets ORM, d'où la lecture colonne par colonne (SQLAlchemy
Core) et des sous-scores en dataclasses, pour rester sous 500 ms
(tests/test_cleanup.py).

Rien ici ne supprime quoi que ce soit : la suppression passe par le flux
existant (suppression sélective « Tout supprimer », aperçu et confirmation)."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Row
from sqlalchemy import select as core_select
from sqlmodel import Session, col, select

from app.models.ignore import IgnoreRule
from app.models.media import EmbyUser, Media, MediaRequest, MediaType, MediaWatch, Torrent
from app.models.settings import Settings
from app.schemas.cleanup import (
    CleanupCandidateDetail,
    CleanupCandidateRead,
    CleanupCandidatesPage,
    CleanupSettings,
)
from app.services.arr_instances import instance_names
from app.services.cleanup_score import PRESETS, CandidateFacts, Evaluation, evaluate, ranked
from app.services.ignores import media_key_of
from app.services.seed_protection import SeedObligation, seed_obligation, seed_policy, strongest
from app.services.seer import seer_configured
from app.services.watch_stats import as_utc, excluded_user_ids

logger = logging.getLogger(__name__)

# Une demande refusée ne protège rien.
_INACTIVE_REQUEST_STATUSES = {"declined"}


def cleanup_settings(settings: Settings | None) -> CleanupSettings:
    raw = settings.cleanup_settings if settings else "{}"
    try:
        return CleanupSettings.model_validate_json(raw or "{}")
    except ValidationError:
        logger.warning("Réglages de l'assistant de nettoyage illisibles : préréglage « Équilibré »")
        return PRESETS["balanced"]


# --- Collecte ---------------------------------------------------------------


def _rows(session: Session, *columns: Any, where: Any = None) -> list[Row[Any]]:
    """Colonnes brutes, sans objet ORM (voir la mesure en tête de module)."""
    query = core_select(*columns)
    if where is not None:
        query = query.where(where)
    return list(session.connection().execute(query).all())


@dataclass(frozen=True)
class _TorrentFacts:
    is_private: bool | None
    completed_on: datetime | None
    added_on: datetime | None
    ratio: float | None
    trackers_json: str


@dataclass
class _WatchFacts:
    users: int = 0
    unfinished: int = 0
    in_progress: list[str] | None = None
    favorites: list[str] | None = None


def _active_users(session: Session, settings: Settings | None, inactive_days: int, now: datetime) -> dict[str, str]:
    """Comptes pris en compte : non désactivés, non exclus des statistiques,
    actifs depuis moins de `inactive_days` jours. Sans activité connue : inactif."""
    excluded = excluded_user_ids(settings)
    cutoff = now - timedelta(days=inactive_days)
    return {
        user.id: user.name
        for user in session.exec(select(EmbyUser)).all()
        if not user.is_disabled
        and user.id not in excluded
        and user.last_activity_at is not None
        and as_utc(user.last_activity_at) >= cutoff
    }


def _watch_facts(session: Session, active: dict[str, str]) -> dict[int, _WatchFacts]:
    facts: dict[int, _WatchFacts] = defaultdict(_WatchFacts)
    if not active:
        return facts
    rows = _rows(
        session,
        col(MediaWatch.media_id),
        col(MediaWatch.emby_user_id),
        col(MediaWatch.played),
        col(MediaWatch.in_progress),
        col(MediaWatch.favorite),
        where=col(MediaWatch.emby_user_id).in_(list(active)),
    )
    for media_id, user_id, played, in_progress, favorite in rows:
        entry = facts[media_id]
        entry.users += 1
        entry.unfinished += 0 if played else 1
        name = active[user_id]
        if in_progress:
            entry.in_progress = [*(entry.in_progress or []), name]
        if favorite:
            entry.favorites = [*(entry.favorites or []), name]
    return facts


def _request_facts(session: Session, settings: Settings | None) -> tuple[dict[int, set[str]], set[int]]:
    """(demandeurs qui n'ont pas tout vu, par média ; médias dont un
    demandeur n'a aucun compte connu). Seer/Ombi désactivé : rien."""
    if not seer_configured(settings):
        return {}, set()
    requests = _rows(
        session,
        col(MediaRequest.media_id),
        col(MediaRequest.requested_by_emby_id),
        col(MediaRequest.requested_by_name),
        col(MediaRequest.status),
    )
    requesters = {emby_id for _, emby_id, _, _ in requests if emby_id}
    played: set[tuple[int, str]] = set()
    if requesters:
        played = {
            (media_id, user_id)
            for media_id, user_id in session.exec(
                select(MediaWatch.media_id, MediaWatch.emby_user_id).where(
                    col(MediaWatch.emby_user_id).in_(list(requesters)), col(MediaWatch.played)
                )
            ).all()
        }
    unwatched: dict[int, set[str]] = defaultdict(set)
    unknown: set[int] = set()
    for media_id, emby_id, name, status in requests:
        if status in _INACTIVE_REQUEST_STATUSES:
            continue
        if not emby_id:
            unknown.add(media_id)
        elif (media_id, emby_id) not in played:
            unwatched[media_id].add(name or emby_id)
    return unwatched, unknown


def _seed_facts(session: Session, settings: Settings | None, now: datetime) -> dict[int, SeedObligation]:
    policy = seed_policy(settings)
    if not policy.enabled:
        return {}
    pending: dict[int, list[SeedObligation]] = defaultdict(list)
    rows = _rows(
        session,
        col(Torrent.media_id),
        col(Torrent.is_private),
        col(Torrent.completed_on),
        col(Torrent.added_on),
        col(Torrent.ratio),
        col(Torrent.trackers_json),
        where=col(Torrent.media_id).is_not(None),
    )
    for media_id, is_private, completed_on, added_on, ratio, trackers_json in rows:
        facts = _TorrentFacts(is_private, completed_on, added_on, ratio, trackers_json)
        obligation = seed_obligation(facts, policy, now)
        if obligation is not None:
            pending[media_id].append(obligation)
    return {media_id: found for media_id, obligations in pending.items() if (found := strongest(obligations))}


def collect_facts(session: Session, now: datetime | None = None) -> tuple[list[CandidateFacts], CleanupSettings]:
    """Faits de chaque média présent sur le disque (fichiers ou torrents)."""
    now = now or datetime.now(UTC)
    settings = session.get(Settings, 1)
    config = cleanup_settings(settings)
    active = _active_users(session, settings, config.inactive_days, now)
    watch = _watch_facts(session, active)
    unwatched, unknown = _request_facts(session, settings)
    seed = _seed_facts(session, settings, now)
    excluded = set(session.exec(select(IgnoreRule.target).where(col(IgnoreRule.kind) == "cleanup")).all())
    names = instance_names(session)

    facts = []
    rows = _rows(
        session,
        col(Media.id),
        col(Media.media_type),
        col(Media.title),
        col(Media.year),
        col(Media.full_reclaimable_bytes),
        col(Media.emby_date_added),
        col(Media.last_played_at),
        col(Media.series_status),
        col(Media.has_poster),
        col(Media.poster_image_tag),
        col(Media.arr_instance_id),
        col(Media.radarr_id),
        col(Media.sonarr_id),
        col(Media.emby_item_id),
        # Rien sur le disque : rien à libérer.
        where=(col(Media.total_size) > 0) | (col(Media.full_reclaimable_bytes) > 0),
    )
    for row in rows:
        media_type = MediaType(row.media_type)
        arr_id = row.sonarr_id if media_type == MediaType.series else row.radarr_id
        seen = watch.get(row.id, _WatchFacts())
        facts.append(
            CandidateFacts(
                media_id=row.id,
                media_type=media_type.value,
                title=row.title,
                year=row.year,
                reclaimable_bytes=row.full_reclaimable_bytes,
                date_added=row.emby_date_added,
                last_played_at=row.last_played_at,
                series_status=row.series_status,
                active_users=seen.users,
                active_unfinished=seen.unfinished,
                in_progress_names=tuple(seen.in_progress or ()),
                favorite_names=tuple(seen.favorites or ()),
                unwatched_requesters=tuple(sorted(unwatched.get(row.id, set()))),
                unknown_requester=row.id in unknown,
                seed_obligation=seed.get(row.id),
                excluded=media_key_of(media_type, arr_id, row.arr_instance_id, row.emby_item_id) in excluded,
                has_poster=row.has_poster,
                poster_image_tag=row.poster_image_tag,
                arr_instance_name=names.get(row.arr_instance_id) if row.arr_instance_id is not None else None,
            )
        )
    return facts, config


# --- Lecture ------------------------------------------------------------------


def _read(evaluation: Evaluation, rank: float) -> CleanupCandidateRead:
    facts = evaluation.facts
    return CleanupCandidateRead(
        media_id=facts.media_id,
        media_type=facts.media_type,
        title=facts.title,
        year=facts.year,
        has_poster=facts.has_poster,
        poster_image_tag=facts.poster_image_tag,
        arr_instance_name=facts.arr_instance_name,
        score=evaluation.score,
        rank=rank,
        reclaimable_bytes=facts.reclaimable_bytes,
        main_reason=evaluation.main_reason,
        malus_in_progress=bool(facts.in_progress_names),
        protections=evaluation.protections,
        last_played_at=as_utc(facts.last_played_at),
        date_added=as_utc(facts.date_added),
        series_status=facts.series_status,
        active_users=facts.active_users,
    )


def _evaluate_all(session: Session, now: datetime) -> tuple[list[Evaluation], dict[int, float]]:
    facts, config = collect_facts(session, now)
    evaluations = [evaluate(f, config, now) for f in facts]
    ranks = {e.facts.media_id: rank for e, rank in ranked(evaluations, config)}
    return evaluations, ranks


_SORTS = {
    "rank": lambda e, ranks: (-ranks.get(e.facts.media_id, -1.0), e.facts.title.lower(), e.facts.media_id),
    "score": lambda e, ranks: (-e.score, e.facts.title.lower(), e.facts.media_id),
    "space": lambda e, ranks: (-e.facts.reclaimable_bytes, e.facts.title.lower(), e.facts.media_id),
    "title": lambda e, ranks: (e.facts.title.lower(), e.facts.media_id),
}


@dataclass(frozen=True)
class CandidateQuery:
    page: int = 1
    page_size: int = 50
    sort: str = "rank"
    media_type: str | None = None
    min_score: int = 0
    search: str = ""
    include_protected: bool = False


def candidates_page(session: Session, query: CandidateQuery, now: datetime | None = None) -> CleanupCandidatesPage:
    now = now or datetime.now(UTC)
    evaluations, ranks = _evaluate_all(session, now)
    needle = query.search.strip().casefold()
    matching = [
        e
        for e in evaluations
        if (query.media_type is None or e.facts.media_type == query.media_type)
        and e.score >= query.min_score
        and (not needle or needle in e.facts.title.casefold())
    ]
    candidates = [e for e in matching if not e.protected]
    shown = matching if query.include_protected else candidates
    # Les médias protégés viennent toujours après les candidats.
    shown.sort(key=lambda e: (e.protected, _SORTS.get(query.sort, _SORTS["rank"])(e, ranks)))
    start = (query.page - 1) * query.page_size
    return CleanupCandidatesPage(
        items=[_read(e, ranks.get(e.facts.media_id, 0.0)) for e in shown[start : start + query.page_size]],
        total=len(shown),
        candidate_count=len(candidates),
        protected_count=len(matching) - len(candidates),
        total_reclaimable_bytes=sum(e.facts.reclaimable_bytes for e in candidates),
    )


def candidate_detail(session: Session, media_id: int, now: datetime | None = None) -> CleanupCandidateDetail | None:
    now = now or datetime.now(UTC)
    evaluations, ranks = _evaluate_all(session, now)
    found = next((e for e in evaluations if e.facts.media_id == media_id), None)
    if found is None:
        return None
    return CleanupCandidateDetail(
        **_read(found, ranks.get(media_id, 0.0)).model_dump(),
        components=[c.read() for c in found.components],
        raw_score=found.raw_score,
        in_progress_users=sorted(found.facts.in_progress_names),
    )
