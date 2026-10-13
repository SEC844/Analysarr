"""Résumé hebdomadaire (optionnel) : croissance de la bibliothèque,
remplissage estimé des disques, meilleurs candidats au nettoyage et torrents
qui sortent bientôt de leur obligation de seed.

Opt-in par abonnement : il n'est construit et envoyé que si au moins un canal
actif est abonné à l'événement `weekly_summary` (job APScheduler du lundi,
voir services/scheduler.py). Lecture seule : uniquement la base (photographies,
cache média), aucune requête vers les services."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlmodel import Session, col, select
from starlette.concurrency import run_in_threadpool

from app.database import engine
from app.models.library_snapshot import LibrarySnapshot
from app.models.media import Torrent
from app.models.settings import Settings
from app.services.cleanup import ranked_candidates
from app.services.forecast import DiskForecast, Trend, load_forecast
from app.services.library_history import library_is_known, library_totals
from app.services.notifications import (
    ChannelTarget,
    Notification,
    channel_targets,
    notification_language,
    send,
    weekly_summary_notification,
)
from app.services.seed_protection import seed_obligation, seed_policy

SUMMARY_EVENT = "weekly_summary"
SkipReason = Literal["no_subscriber", "library_unknown"]
WEEK_DAYS = 7
TOP_CANDIDATES = 10
SEED_NAMES = 5


@dataclass(frozen=True)
class CandidateLine:
    title: str
    year: int | None
    reclaimable_bytes: int
    score: int


@dataclass(frozen=True)
class SeedRelease:
    name: str
    until: datetime | None


@dataclass(frozen=True)
class WeeklySummary:
    media_count: int
    library_size: int
    # Variation de la taille de la bibliothèque sur 7 jours (mesures réelles,
    # pas une régression) ; None sans photographie d'il y a une semaine.
    week_growth: int | None
    library_trend: Trend | None
    disks: list[DiskForecast]
    candidate_count: int
    candidate_bytes: int
    top_candidates: list[CandidateLine]
    # None : protection du seed désactivée (section omise).
    seed_releases: list[SeedRelease] | None


def _week_growth(session: Session, size: int, today: date) -> int | None:
    """Taille d'il y a 7 jours : la photographie la plus récente datée d'au
    moins une semaine (un jour manquant ne fait pas disparaître le chiffre)."""
    past = session.exec(
        select(LibrarySnapshot)
        .where(col(LibrarySnapshot.day) <= today - timedelta(days=WEEK_DAYS))
        .order_by(col(LibrarySnapshot.day).desc())
        .limit(1)
    ).first()
    return None if past is None else size - past.total_size


def _seed_releases(session: Session, settings: Settings | None, now: datetime) -> list[SeedRelease] | None:
    """Torrents protégés aujourd'hui qui ne le seront plus dans 7 jours, à
    ratio inchangé (il ne peut que monter : c'est une date au plus tard)."""
    policy = seed_policy(settings)
    if not policy.enabled:
        return None
    later = now + timedelta(days=WEEK_DAYS)
    releases = []
    for torrent in session.exec(select(Torrent).where(col(Torrent.media_id).is_not(None))).all():
        current = seed_obligation(torrent, policy, now)
        if current is not None and seed_obligation(torrent, policy, later) is None:
            releases.append(SeedRelease(name=torrent.name, until=current.until))
    return sorted(releases, key=lambda r: (r.until or later, r.name.casefold()))


def build_weekly_summary(session: Session, now: datetime | None = None) -> WeeklySummary | None:
    """None : bibliothèque jamais analysée (rien de fiable à résumer)."""
    if not library_is_known(session):
        return None
    now = now or datetime.now(UTC)
    totals = library_totals(session)
    size = totals.movie_size + totals.series_size
    forecast = load_forecast(session)
    candidates = ranked_candidates(session, now)
    return WeeklySummary(
        media_count=totals.movie_count + totals.series_count,
        library_size=size,
        week_growth=_week_growth(session, size, now.date()),
        library_trend=forecast.library_trend,
        disks=forecast.disks,
        candidate_count=len(candidates),
        candidate_bytes=sum(e.facts.reclaimable_bytes for e, _ in candidates),
        top_candidates=[
            CandidateLine(e.facts.title, e.facts.year, e.facts.reclaimable_bytes, e.score)
            for e, _ in candidates[:TOP_CANDIDATES]
        ],
        seed_releases=_seed_releases(session, session.get(Settings, 1), now),
    )


# --- Envoi --------------------------------------------------------------------


@dataclass(frozen=True)
class SummaryDelivery:
    """Résultat d'un envoi : erreur par canal (None = envoyé), ou la raison de
    ne rien avoir envoyé."""

    results: dict[str, str | None]
    skipped: "SkipReason | None" = None


def _prepare() -> tuple[list[ChannelTarget], Notification | None, "SkipReason | None"]:
    with Session(engine) as session:
        targets = [target for target in channel_targets(session) if target.wants(SUMMARY_EVENT)]
        if not targets:
            return [], None, "no_subscriber"
        summary = build_weekly_summary(session)
        if summary is None:
            return targets, None, "library_unknown"
        language = notification_language(session.get(Settings, 1))
        return targets, weekly_summary_notification(language, summary), None


async def send_weekly_summary() -> SummaryDelivery:
    """Construit (base lue hors de la boucle asyncio) puis envoie le résumé
    aux seuls canaux abonnés. Personne d'abonné : rien n'est calculé."""
    targets, notification, skipped = await run_in_threadpool(_prepare)
    if notification is None:
        return SummaryDelivery(results={}, skipped=skipped)
    return SummaryDelivery(results=await send(targets, notification))
