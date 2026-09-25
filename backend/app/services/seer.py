"""Demandes du gestionnaire de demandes rattachées aux médias : qui a
demandé, quand, qui a approuvé. Lecture seule.

Deux gestionnaires, au choix (`Settings.seer_type`) : Seer (Overseerr,
Jellyseerr, Seerr : même API) ou Ombi. Les noms internes `seer_*` désignent
le gestionnaire configuré, quel qu'il soit — comme `emby_*` pour le serveur
multimédia. Les deux produisent les MÊMES lignes `MediaRequest`.

Rattachement par identifiant TMDB (films) ou TVDB (séries), déjà connus de
Radarr/Sonarr. Aucune image n'est jamais récupérée depuis le gestionnaire :
l'avatar affiché est celui du serveur multimédia (même compte), déjà relayé
par le backend."""

from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.clients.ombi import OmbiClient
from app.clients.seer import SeerClient
from app.models.media import EmbyUser, Media, MediaRequest, MediaType
from app.models.settings import Settings
from app.schemas.media import MediaRequestRead, SeerUserRead
from app.services.watch_stats import as_utc, parse_emby_date

_STATUSES = {1: "pending", 2: "approved", 3: "declined", 4: "failed", 5: "completed"}

RequestKey = tuple[str, int]  # ("movie", tmdbId) | ("tv", tvdbId)
RequestEntry = tuple[RequestKey, dict[str, Any]]
RequestClient = SeerClient | OmbiClient


def request_manager_name(settings: Settings | None) -> str:
    return "Ombi" if settings is not None and settings.seer_type == "ombi" else "Seer"


def seer_client(settings: Settings | None) -> RequestClient | None:
    """Client du gestionnaire de demandes, ou None s'il est désactivé ou
    incomplet."""
    if settings is None or not settings.seer_enabled or not settings.seer_url or not settings.seer_api_key:
        return None
    if settings.seer_type == "ombi":
        return OmbiClient(settings.seer_url, settings.seer_api_key)
    return SeerClient(settings.seer_url, settings.seer_api_key)


def seer_configured(settings: Settings | None) -> bool:
    return seer_client(settings) is not None


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
    status_code = raw.get("status")
    status = _STATUSES.get(status_code, "pending") if isinstance(status_code, int) else "pending"
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


# --- Ombi -------------------------------------------------------------------
# Champs vérifiés dans le code source d'Ombi (entités `MovieRequests`,
# `TvRequests`, `ChildRequests`, `OmbiUser`). Ombi ne mémorise PAS qui a
# approuvé une demande : l'approbation est affichée sans nom, jamais comme
# « automatique » (Ombi ne le dit pas).

# `OmbiUser.UserType` : EmbyUser = 3, JellyfinUser = 5. `ProviderUserId` est
# alors l'identifiant du compte sur le serveur multimédia.
_OMBI_MEDIA_SERVER_USERS = {3, 5}


def _ombi_date(value: Any) -> datetime | None:
    """`DateTime` .NET : une demande jamais faite vaut 0001-01-01."""
    parsed = parse_emby_date(value)
    return parsed if parsed is not None and parsed.year > 1 else None


def _ombi_status(approved: Any, denied: Any, available: Any) -> str:
    if available:
        return "completed"
    if denied:
        return "declined"
    return "approved" if approved else "pending"


def _ombi_user(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    user = raw.get("requestedUser") or {}
    candidates = (user.get("alias"), user.get("userName"), raw.get("requestedByAlias"))
    name = next((value for value in candidates if isinstance(value, str) and value), None)
    provider_id = user.get("providerUserId")
    if user.get("userType") in _OMBI_MEDIA_SERVER_USERS and isinstance(provider_id, str) and provider_id:
        return name, provider_id.replace("-", "").lower()
    return name, None


def _ombi_fields(
    raw: dict[str, Any], *, status: str, requested_at: datetime | None, is_4k: bool, seasons: str = ""
) -> dict[str, Any]:
    name, emby_id = _ombi_user(raw)
    return {
        "seer_request_id": raw["id"],
        "seer_media_id": None,
        "status": status,
        "is_4k": is_4k,
        "seasons": seasons,
        "requested_at": requested_at,
        "requested_by_name": name,
        "requested_by_emby_id": emby_id,
        "modified_by_name": None,
        "modified_by_emby_id": None,
        "auto_approved": False,
    }


def parse_ombi_movie(raw: dict[str, Any]) -> list[RequestEntry]:
    """Une demande de film Ombi porte la demande normale ET la demande 4K
    (`has4KRequest`), chacune avec son propre état : deux lignes."""
    tmdb_id = raw.get("theMovieDbId")
    if not isinstance(tmdb_id, int) or tmdb_id <= 0 or not isinstance(raw.get("id"), int):
        return []
    key: RequestKey = ("movie", tmdb_id)
    entries: list[RequestEntry] = []
    requested_at = _ombi_date(raw.get("requestedDate"))
    if requested_at is not None or not raw.get("has4KRequest"):
        status = _ombi_status(raw.get("approved"), raw.get("denied"), raw.get("available"))
        entries.append((key, _ombi_fields(raw, status=status, requested_at=requested_at, is_4k=False)))
    if raw.get("has4KRequest"):
        status = _ombi_status(raw.get("approved4K"), raw.get("denied4K"), raw.get("available4K"))
        requested_4k = _ombi_date(raw.get("requestedDate4k"))
        entries.append((key, _ombi_fields(raw, status=status, requested_at=requested_4k, is_4k=True)))
    return entries


def parse_ombi_series(raw: dict[str, Any]) -> list[RequestEntry]:
    """Une demande de série Ombi regroupe une demande enfant par utilisateur,
    chacune avec ses saisons et son état."""
    tvdb_id = raw.get("tvDbId")
    if not isinstance(tvdb_id, int) or tvdb_id <= 0:
        return []
    entries: list[RequestEntry] = []
    for child in raw.get("childRequests") or []:
        if not isinstance(child, dict) or not isinstance(child.get("id"), int):
            continue
        numbers = {s.get("seasonNumber") for s in child.get("seasonRequests") or [] if isinstance(s, dict)}
        seasons = ",".join(str(n) for n in sorted(n for n in numbers if isinstance(n, int)))
        status = _ombi_status(child.get("approved"), child.get("denied"), child.get("available"))
        requested_at = _ombi_date(child.get("requestedDate"))
        entries.append(
            (
                ("tv", tvdb_id),
                _ombi_fields(child, status=status, requested_at=requested_at, is_4k=False, seasons=seasons),
            )
        )
    return entries


def _index(entries: list[RequestEntry]) -> dict[RequestKey, list[dict[str, Any]]]:
    index: dict[RequestKey, list[dict[str, Any]]] = {}
    for key, fields in entries:
        index.setdefault(key, []).append(fields)
    return index


def index_requests(raw_requests: list[dict[str, Any]]) -> dict[RequestKey, list[dict[str, Any]]]:
    return _index([parsed for raw in raw_requests if (parsed := parse_request(raw)) is not None])


async def fetch_request_index(client: RequestClient) -> dict[RequestKey, list[dict[str, Any]]]:
    """Demandes du gestionnaire configuré, indexées par identifiant de média."""
    if isinstance(client, OmbiClient):
        movies = [entry for raw in await client.get_movie_requests() for entry in parse_ombi_movie(raw)]
        series = [entry for raw in await client.get_tv_requests() for entry in parse_ombi_series(raw)]
        return _index(movies + series)
    return index_requests(await client.get_requests())


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
