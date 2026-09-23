"""Statistiques de visionnage par média, depuis l'API Emby.

Pour chaque utilisateur Emby actif, l'état de lecture n'est demandé qu'aux
éléments qu'il peut voir (bibliothèques auxquelles il a accès) : un compte
sans accès à un film n'entre donc pas dans son quota « 3/10 ». Les comptes
désactivés dans Emby et ceux exclus dans les réglages ne sont jamais comptés.

- Scan : quatre appels par utilisateur pour TOUTE la bibliothèque (films,
  séries accessibles, épisodes vus, épisodes en cours), quel que soit le
  nombre de médias.
- Fiche média : mêmes appels restreints à ce seul média, pour des chiffres à
  jour là où la décision se prend (repli sur le dernier scan si Emby est
  injoignable).

Aucune donnée d'utilisateur Emby ne quitte le serveur hors de l'interface
authentifiée d'Analysarr ; les avatars sont relayés par le backend (la clé
API Emby n'atteint jamais le navigateur)."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, overload

import httpx
from sqlmodel import Session, col, delete, select

from app.clients.emby import EmbyClient, media_server_client
from app.models.media import EmbyUser, Media, MediaType, MediaWatch
from app.models.settings import Settings
from app.schemas.media import MediaWatchStats, WatchUser

logger = logging.getLogger(__name__)

# Appels Emby simultanés au maximum (serveur local, mais pas de rafale inutile).
_CONCURRENCY = 8


@dataclass
class UserWatchData:
    # Films visibles par l'utilisateur -> son UserData.
    movies: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Séries visibles par l'utilisateur.
    series: set[str] = field(default_factory=set)
    episodes_played: dict[str, int] = field(default_factory=dict)
    series_resuming: set[str] = field(default_factory=set)
    series_last_played: dict[str, datetime] = field(default_factory=dict)


def parse_emby_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@overload
def as_utc(value: datetime) -> datetime: ...
@overload
def as_utc(value: None) -> None: ...
def as_utc(value: datetime | None) -> datetime | None:
    """SQLite rend des dates naïves : elles ont toujours été enregistrées en UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def excluded_user_ids(settings: Settings | None) -> set[str]:
    try:
        values = json.loads(settings.excluded_emby_user_ids) if settings else []
    except (TypeError, ValueError):
        return set()
    return {v for v in values if isinstance(v, str)}


def users_from_api(raw: list[dict[str, Any]]) -> list[EmbyUser]:
    return [
        EmbyUser(
            id=str(u["Id"]),
            name=str(u.get("Name") or "?"),
            image_tag=u.get("PrimaryImageTag"),
            is_disabled=bool((u.get("Policy") or {}).get("IsDisabled")),
        )
        for u in raw
        if u.get("Id")
    ]


async def fetch_user_watch(
    emby: EmbyClient, user_id: str, *, movie_id: str | None = None, series_id: str | None = None
) -> UserWatchData:
    """Sans identifiant : toute la bibliothèque (scan). Avec `movie_id` ou
    `series_id` : ce seul média (rafraîchissement d'une fiche)."""
    data = UserWatchData()
    if series_id is None:
        for item in await emby.get_user_items(user_id, "Movie", ids=movie_id):
            data.movies[item["Id"]] = item.get("UserData") or {}
    if movie_id is None:
        for item in await emby.get_user_items(user_id, "Series", ids=series_id):
            data.series.add(item["Id"])
        for flt in ("IsPlayed", "IsResumable"):
            for episode in await emby.get_user_items(user_id, "Episode", parent_id=series_id, filters=flt):
                sid = episode.get("SeriesId")
                if not sid:
                    continue
                if flt == "IsPlayed":
                    data.episodes_played[sid] = data.episodes_played.get(sid, 0) + 1
                else:
                    data.series_resuming.add(sid)
                played_at = parse_emby_date((episode.get("UserData") or {}).get("LastPlayedDate"))
                if played_at and (sid not in data.series_last_played or played_at > data.series_last_played[sid]):
                    data.series_last_played[sid] = played_at
    return data


async def collect_watch_data(
    emby: EmbyClient, users: list[EmbyUser], *, movie_id: str | None = None, series_id: str | None = None
) -> dict[str, UserWatchData]:
    """Données de visionnage de chaque utilisateur actif. Un utilisateur dont
    les appels échouent est simplement absent du résultat."""
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def one(user: EmbyUser) -> tuple[str, UserWatchData | None]:
        async with semaphore:
            try:
                return user.id, await fetch_user_watch(emby, user.id, movie_id=movie_id, series_id=series_id)
            except (httpx.HTTPError, ValueError, KeyError):
                # Cet utilisateur manque aux statistiques, les autres restent justes.
                logger.warning("Visionnage illisible pour l'utilisateur %s", user.id, exc_info=True)
                return user.id, None

    pairs = await asyncio.gather(*(one(u) for u in users if not u.is_disabled))
    return {user_id: data for user_id, data in pairs if data is not None}


def build_watch_rows(media: Media, users: list[EmbyUser], data: dict[str, UserWatchData]) -> list[MediaWatch]:
    rows: list[MediaWatch] = []
    item_id = media.emby_item_id
    if not item_id:
        return rows
    for user in users:
        user_data = data.get(user.id)
        if user.is_disabled or user_data is None:
            continue
        if media.media_type == MediaType.movie:
            if item_id not in user_data.movies:
                continue  # film hors des bibliothèques de cet utilisateur
            state = user_data.movies[item_id]
            played = bool(state.get("Played"))
            percent = 100.0 if played else min(float(state.get("PlayedPercentage") or 0), 100.0)
            rows.append(
                MediaWatch(
                    media_id=media.id or 0,
                    emby_user_id=user.id,
                    played=played,
                    progress=round(percent, 1),
                    in_progress=not played and percent > 0,
                    last_played_at=parse_emby_date(state.get("LastPlayedDate")),
                )
            )
        else:
            if item_id not in user_data.series:
                continue  # série hors des bibliothèques de cet utilisateur
            watched = user_data.episodes_played.get(item_id, 0)
            if media.episode_count:
                watched = min(watched, media.episode_count)
            played = media.episode_count > 0 and watched >= media.episode_count
            rows.append(
                MediaWatch(
                    media_id=media.id or 0,
                    emby_user_id=user.id,
                    played=played,
                    progress=watched,
                    in_progress=not played and (watched > 0 or item_id in user_data.series_resuming),
                    last_played_at=user_data.series_last_played.get(item_id),
                )
            )
    return rows


def apply_aggregates(media: Media, rows: list[MediaWatch], excluded: set[str]) -> None:
    counted = [r for r in rows if r.emby_user_id not in excluded]
    media.watch_user_count = len(counted)
    media.watch_played_count = sum(1 for r in counted if r.played)
    media.watch_in_progress_count = sum(1 for r in counted if r.in_progress)
    dates = [as_utc(r.last_played_at) for r in counted if r.last_played_at]
    media.last_played_at = max(dates) if dates else None


def recompute_all_aggregates(session: Session, settings: Settings | None) -> None:
    """Après un changement des utilisateurs exclus : recalcul immédiat des
    agrégats de toute la bibliothèque, sans attendre le prochain scan."""
    excluded = excluded_user_ids(settings)
    rows_by_media: dict[int, list[MediaWatch]] = {}
    for row in session.exec(select(MediaWatch)).all():
        rows_by_media.setdefault(row.media_id, []).append(row)
    for media in session.exec(select(Media)).all():
        apply_aggregates(media, rows_by_media.get(media.id or 0, []), excluded)
        session.add(media)
    session.commit()


async def refresh_media_watch(session: Session, media: Media, settings: Settings | None) -> bool:
    """Rafraîchit en direct le visionnage d'un seul média. False (données du
    dernier scan conservées) si Emby n'est pas configuré ou injoignable."""
    emby = media_server_client(settings)
    if not (media.emby_item_id and emby):
        return False
    try:
        users = users_from_api(await emby.get_users())
    except (httpx.HTTPError, ValueError):
        # Les chiffres du dernier scan restent affichés (live=false).
        logger.debug("Visionnage en direct indisponible", exc_info=True)
        return False

    is_movie = media.media_type == MediaType.movie
    active = [u for u in users if not u.is_disabled]
    data = await collect_watch_data(
        emby,
        users,
        movie_id=media.emby_item_id if is_movie else None,
        series_id=None if is_movie else media.emby_item_id,
    )
    if len(data) < len(active):
        return False  # réponse partielle : mieux vaut le dernier scan complet

    session.exec(delete(EmbyUser))
    for user in users:
        session.add(user)
    session.exec(delete(MediaWatch).where(col(MediaWatch.media_id) == media.id))
    rows = build_watch_rows(media, users, data)
    for row in rows:
        session.add(row)
    apply_aggregates(media, rows, excluded_user_ids(settings))
    session.add(media)
    session.commit()
    return True


def build_watch_stats(session: Session, media: Media, settings: Settings | None, live: bool) -> MediaWatchStats:
    excluded = excluded_user_ids(settings)
    users = {u.id: u for u in session.exec(select(EmbyUser)).all()}
    rows = [
        r
        for r in session.exec(select(MediaWatch).where(MediaWatch.media_id == media.id)).all()
        if r.emby_user_id not in excluded and r.emby_user_id in users
    ]
    watch_users = sorted(
        (
            WatchUser(
                id=r.emby_user_id,
                name=users[r.emby_user_id].name,
                image_tag=users[r.emby_user_id].image_tag,
                played=r.played,
                in_progress=r.in_progress,
                progress=r.progress,
                last_played_at=as_utc(r.last_played_at),
            )
            for r in rows
        ),
        key=lambda u: (not u.played, -u.progress, u.name.lower()),
    )
    played = [(u.last_played_at, u) for u in watch_users if u.last_played_at is not None]
    last = max(played, key=lambda pair: pair[0])[1] if played else None
    return MediaWatchStats(
        available=bool(media.emby_item_id),
        live=live,
        total_episodes=media.episode_count if media.media_type == MediaType.series else None,
        users=watch_users,
        played_count=sum(1 for u in watch_users if u.played),
        in_progress_count=sum(1 for u in watch_users if u.in_progress),
        last_played_at=last.last_played_at if last else None,
        last_played_by=last.name if last else None,
        date_added=as_utc(media.emby_date_added),
    )
