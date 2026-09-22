"""Demandes Seer rattachées aux médias : qui a demandé, quand, qui a approuvé
— et retrait de la demande quand le média est supprimé.

Rattachement par identifiant TMDB (films) ou TVDB (séries), déjà connus de
Radarr/Sonarr. Aucune image n'est jamais récupérée depuis Seer : l'avatar
affiché est celui d'Emby (même compte, via `jellyfinUserId`), déjà relayé par
le backend."""

from typing import Any

import httpx
from sqlmodel import Session, delete, select

from app.clients.seer import SeerClient
from app.models.media import EmbyUser, Media, MediaRequest, MediaType
from app.models.settings import Settings
from app.schemas.media import DeleteStepResult, MediaRequestRead, SeerUserRead
from app.services.watch_stats import as_utc, parse_emby_date

_STATUSES = {1: "pending", 2: "approved", 3: "declined", 4: "failed", 5: "completed"}

RequestKey = tuple[str, int]  # ("movie", tmdbId) | ("tv", tvdbId)


def seer_configured(settings: Settings | None) -> bool:
    return bool(settings and settings.seer_enabled and settings.seer_url and settings.seer_api_key)


def _user_name(user: dict[str, Any]) -> str | None:
    for key in ("displayName", "username", "jellyfinUsername", "plexUsername"):
        if isinstance(user.get(key), str) and user[key]:
            return user[key]
    return None


def _emby_id(user: dict[str, Any]) -> str | None:
    value = user.get("jellyfinUserId")
    return value.replace("-", "").lower() if isinstance(value, str) and value else None


def parse_request(raw: dict[str, Any]) -> tuple[RequestKey, dict[str, Any]] | None:
    media = raw.get("media") or {}
    media_type = media.get("mediaType") or raw.get("type")
    external_id = media.get("tmdbId") if media_type == "movie" else media.get("tvdbId")
    if media_type not in ("movie", "tv") or not isinstance(external_id, int) or not isinstance(raw.get("id"), int):
        return None

    requested_by = raw.get("requestedBy") or {}
    modified_by = raw.get("modifiedBy") or None
    status = _STATUSES.get(raw.get("status"), "pending")
    auto_approved = status in ("approved", "completed", "failed") and (
        modified_by is None or modified_by.get("id") == requested_by.get("id")
    )
    fields = {
        "seer_request_id": raw["id"],
        "seer_media_id": media.get("id") if isinstance(media.get("id"), int) else None,
        "status": status,
        "is_4k": bool(raw.get("is4k")),
        "seasons": ",".join(
            str(s["seasonNumber"]) for s in raw.get("seasons") or [] if isinstance(s.get("seasonNumber"), int)
        ),
        "requested_at": parse_emby_date(raw.get("createdAt")),
        "requested_by_name": _user_name(requested_by),
        "requested_by_emby_id": _emby_id(requested_by),
        "modified_by_name": None if auto_approved or modified_by is None else _user_name(modified_by),
        "modified_by_emby_id": None if auto_approved or modified_by is None else _emby_id(modified_by),
        "auto_approved": auto_approved,
    }
    return (media_type, external_id), fields


def index_requests(raw_requests: list[dict[str, Any]]) -> dict[RequestKey, list[dict[str, Any]]]:
    index: dict[RequestKey, list[dict[str, Any]]] = {}
    for raw in raw_requests:
        parsed = parse_request(raw)
        if parsed is not None:
            index.setdefault(parsed[0], []).append(parsed[1])
    return index


def _requested_order(row: MediaRequest) -> float:
    """Plus ancienne demande d'abord ; date inconnue en dernier."""
    requested_at = as_utc(row.requested_at)
    return requested_at.timestamp() if requested_at else float("inf")


def build_request_rows(media: Media, index: dict[RequestKey, list[dict[str, Any]]]) -> list[MediaRequest]:
    key: RequestKey | None = (
        ("movie", media.tmdb_id)
        if media.media_type == MediaType.movie and media.tmdb_id
        else ("tv", media.tvdb_id)
        if media.media_type == MediaType.series and media.tvdb_id
        else None
    )
    rows = [MediaRequest(media_id=media.id or 0, **fields) for fields in index.get(key, [])] if key else []
    oldest = min(rows, key=_requested_order, default=None)
    media.requested_by = oldest.requested_by_name if oldest else None
    return rows


def build_requests_read(session: Session, media: Media) -> list[MediaRequestRead]:
    rows = session.exec(select(MediaRequest).where(MediaRequest.media_id == media.id)).all()
    avatars = {u.id.replace("-", "").lower(): u for u in session.exec(select(EmbyUser)).all()}

    def user(name: str | None, emby_id: str | None) -> SeerUserRead | None:
        if not name:
            return None
        emby_user = avatars.get(emby_id) if emby_id else None
        return SeerUserRead(
            name=name,
            emby_user_id=emby_user.id if emby_user else None,
            image_tag=emby_user.image_tag if emby_user else None,
        )

    return [
        MediaRequestRead(
            status=r.status,
            is_4k=r.is_4k,
            seasons=[int(s) for s in r.seasons.split(",") if s],
            requested_at=as_utc(r.requested_at),
            requested_by=user(r.requested_by_name, r.requested_by_emby_id),
            modified_by=user(r.modified_by_name, r.modified_by_emby_id),
            auto_approved=r.auto_approved,
        )
        for r in sorted(rows, key=_requested_order)
    ]
