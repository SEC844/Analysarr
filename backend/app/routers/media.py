import json
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session, select

from app.clients.emby import EmbyClient
from app.clients.qbittorrent import QbittorrentAuthError
from app.database import get_session
from app.models.media import Media, MediaFile, Torrent
from app.models.settings import Settings
from app.schemas.media import (
    CrossSeedSearchResult,
    DeleteExecuteResult,
    DeletePreview,
    HardlinkRepairPreview,
    HardlinkRepairResult,
    MediaDeleteFootprint,
    MediaDeleteSelection,
    MediaDeleteSelectionResult,
    MediaDetail,
    MediaFileRead,
    MediaListItem,
    MediaListResponse,
    MediaWatchStats,
    TorrentRead,
    TrackerRead,
)
from app.services.cascade_delete import build_delete_preview, execute_delete
from app.services.cross_seed import trigger_cross_seed_search
from app.services.hardlink_repair import build_repair_preview, execute_repair
from app.services.media_delete import build_delete_footprint, execute_media_delete
from app.services.poster_cache import read_cached_poster, safe_image_type, write_cached_poster
from app.services.seer import build_requests_read, seer_configured
from app.services.watch_stats import as_utc, build_watch_stats, refresh_media_watch

router = APIRouter()


def _is_cross_seed(torrent: Torrent) -> bool:
    haystacks = (torrent.category, torrent.save_path, torrent.content_path)
    return any(h and "cross-seed" in h.lower() for h in haystacks)


def _to_list_item(media: Media, seer_enabled: bool = False) -> MediaListItem:
    return MediaListItem(
        id=media.id,
        media_type=media.media_type.value,
        title=media.title,
        year=media.year,
        statuses=[s for s in media.statuses.split(",") if s],
        reclaimable_bytes=media.reclaimable_bytes,
        total_size=media.total_size,
        has_poster=media.has_poster,
        poster_image_tag=media.poster_image_tag,
        last_scanned_at=media.last_scanned_at,
        has_emby_item=bool(media.emby_item_id),
        date_added=as_utc(media.emby_date_added),
        watch_user_count=media.watch_user_count,
        watch_played_count=media.watch_played_count,
        watch_in_progress_count=media.watch_in_progress_count,
        last_played_at=as_utc(media.last_played_at),
        # Seer désactivé : aucune trace dans l'interface, même d'un scan passé.
        requested_by=media.requested_by if seer_enabled else None,
    )


def _matches_watch_filter(media: Media, watch: str) -> bool:
    if watch == "never":
        return media.watch_user_count > 0 and media.watch_played_count == 0 and media.watch_in_progress_count == 0
    if watch == "in_progress":
        return media.watch_in_progress_count > 0
    if watch == "all":
        return media.watch_user_count > 0 and media.watch_played_count == media.watch_user_count
    return True


def _idle_since(media: Media) -> datetime | None:
    """Dernière activité connue : dernière lecture, sinon date d'ajout."""
    return as_utc(media.last_played_at) or as_utc(media.emby_date_added)


@router.get("", response_model=MediaListResponse)
def list_media(
    status: Optional[str] = Query(None, description="doublon | orphelin_qbit | non_hardlink | tracker_unique | sain"),
    media_type: Optional[str] = Query(None, description="movie | series"),
    watch: Optional[str] = Query(None, description="never | in_progress | all"),
    search: Optional[str] = None,
    sort: str = Query("title", description="title | year | size | last_played | cleanup"),
    session: Session = Depends(get_session),
) -> MediaListResponse:
    medias = list(session.exec(select(Media)).all())

    if media_type:
        medias = [m for m in medias if m.media_type.value == media_type]
    if search:
        needle = search.lower()
        medias = [m for m in medias if needle in m.title.lower()]
    if status:
        if status == "sain":
            medias = [m for m in medias if not m.statuses]
        else:
            medias = [m for m in medias if status in m.statuses.split(",")]
    if watch:
        medias = [m for m in medias if _matches_watch_filter(m, watch)]

    now = datetime.now(timezone.utc)
    if sort == "year":
        medias.sort(key=lambda m: m.year or 0, reverse=True)
    elif sort == "size":
        medias.sort(key=lambda m: m.reclaimable_bytes, reverse=True)
    elif sort == "last_played":
        # Plus longtemps sans lecture en premier ; jamais lus d'abord, du plus
        # anciennement ajouté au plus récent.
        medias.sort(key=lambda m: (m.last_played_at is not None, _idle_since(m) or now))
    elif sort == "cleanup":
        # Candidats au nettoyage : poids × jours sans activité — un gros
        # fichier jamais regardé depuis un an passe devant un petit fichier vu
        # le mois dernier.
        def cleanup_score(m: Media) -> float:
            since = _idle_since(m)
            idle_days = max((now - since).days, 1) if since else 1
            return (m.total_size or 0) * idle_days

        medias.sort(key=cleanup_score, reverse=True)
    else:
        medias.sort(key=lambda m: m.title.lower())

    seer_enabled = seer_configured(session.get(Settings, 1))
    return MediaListResponse(items=[_to_list_item(m, seer_enabled) for m in medias], total=len(medias))


@router.get("/{media_id}", response_model=MediaDetail)
def get_media(media_id: int, session: Session = Depends(get_session)) -> MediaDetail:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")

    files = session.exec(select(MediaFile).where(MediaFile.media_id == media_id)).all()
    torrents = session.exec(select(Torrent).where(Torrent.media_id == media_id)).all()
    seer_enabled = seer_configured(session.get(Settings, 1))

    return MediaDetail(
        **_to_list_item(media, seer_enabled).model_dump(),
        requests=build_requests_read(session, media) if seer_enabled else [],
        radarr_id=media.radarr_id,
        sonarr_id=media.sonarr_id,
        emby_item_id=media.emby_item_id,
        files=[
            MediaFileRead(id=f.id, path=f.path, size=f.size, episode_label=f.episode_label, is_current=f.is_current)
            for f in files
        ],
        torrents=[
            TorrentRead(
                id=t.id,
                hash=t.hash,
                name=t.name,
                save_path=t.save_path,
                content_path=t.content_path,
                size=t.size,
                is_cross_seed=_is_cross_seed(t),
                is_hardlinked=t.is_hardlinked,
                matched_by_name=t.matched_by_name,
                repairable=t.repairable,
                ratio=t.ratio,
                seeders=t.seeders,
                leechers=t.leechers,
                added_on=t.added_on,
                completed_on=t.completed_on,
                trackers=[TrackerRead(**d) for d in json.loads(t.trackers_json)],
            )
            for t in torrents
        ],
        missing_emby_episodes=[e for e in media.missing_emby_episodes.split(",") if e],
    )


@router.get("/{media_id}/poster")
async def get_poster(media_id: int, session: Session = Depends(get_session)) -> Response:
    media = session.get(Media, media_id)
    if media is None or not media.emby_item_id:
        raise HTTPException(404, "Pas de jaquette disponible.")

    cached = read_cached_poster(media.emby_item_id, media.poster_image_tag)
    if cached is not None:
        content, content_type = cached
    else:
        settings = session.get(Settings, 1)
        if settings is None or not settings.emby_url or not settings.emby_api_key:
            raise HTTPException(404, "Emby non configuré.")
        emby = EmbyClient(settings.emby_url, settings.emby_api_key)
        result = await emby.fetch_poster(media.emby_item_id)
        if result is None:
            raise HTTPException(404, "Jaquette introuvable.")
        content = result[0]
        content_type = safe_image_type(result[1])
        if content_type is None:
            raise HTTPException(404, "Jaquette introuvable.")
        write_cached_poster(media.emby_item_id, media.poster_image_tag, content, content_type)

    # L'URL est déjà propre à cette version précise de la jaquette (voir
    # `?v=` côté frontend, lib/api.ts::posterUrl) : le contenu d'UNE URL
    # donnée ne change jamais, le navigateur peut donc la garder en cache
    # indéfiniment sans risque de servir une jaquette périmée.
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{media_id}/watch", response_model=MediaWatchStats)
async def get_media_watch(media_id: int, session: Session = Depends(get_session)) -> MediaWatchStats:
    """Visionnage par utilisateur Emby, rafraîchi en direct à l'ouverture de
    la fiche (repli sur le dernier scan si Emby est injoignable)."""
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    settings = session.get(Settings, 1)
    live = await refresh_media_watch(session, media, settings)
    return build_watch_stats(session, media, settings, live)


@router.post("/{media_id}/delete/preview", response_model=DeletePreview)
def delete_preview(media_id: int, session: Session = Depends(get_session)) -> DeletePreview:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    return build_delete_preview(session, media)


@router.post("/{media_id}/delete/execute", response_model=DeleteExecuteResult)
async def delete_execute(media_id: int, session: Session = Depends(get_session)) -> DeleteExecuteResult:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    settings = session.get(Settings, 1)
    if settings is None:
        raise HTTPException(400, "Configuration manquante.")
    return await execute_delete(session, media, settings)


@router.get("/{media_id}/delete-selection/footprint", response_model=MediaDeleteFootprint)
async def delete_selection_footprint(media_id: int, session: Session = Depends(get_session)) -> MediaDeleteFootprint:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    settings = session.get(Settings, 1)
    if settings is None:
        raise HTTPException(400, "Configuration manquante.")
    return await build_delete_footprint(session, media, settings)


@router.post("/{media_id}/delete-selection", response_model=MediaDeleteSelectionResult)
async def delete_selection(
    media_id: int, payload: MediaDeleteSelection, session: Session = Depends(get_session)
) -> MediaDeleteSelectionResult:
    if not payload.torrent_ids and not payload.media_file_ids:
        raise HTTPException(400, "Aucun élément sélectionné.")
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    settings = session.get(Settings, 1)
    if settings is None:
        raise HTTPException(400, "Configuration manquante.")
    return await execute_media_delete(session, media, settings, payload)


@router.post("/{media_id}/cross-seed-search", response_model=CrossSeedSearchResult)
async def cross_seed_search(
    media_id: int,
    scope: str = Query("episode", description="episode | season | series (season/series : séries uniquement)"),
    session: Session = Depends(get_session),
) -> CrossSeedSearchResult:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    if scope not in ("episode", "season", "series"):
        raise HTTPException(400, "scope invalide : episode | season | series.")
    settings = session.get(Settings, 1)
    if settings is None:
        raise HTTPException(400, "Configuration manquante.")
    torrents = session.exec(select(Torrent).where(Torrent.media_id == media_id)).all()
    files = session.exec(select(MediaFile).where(MediaFile.media_id == media_id)).all()
    return await trigger_cross_seed_search(settings, [t.hash for t in torrents], list(files), scope=scope)


@router.post("/{media_id}/hardlink-repair/preview", response_model=HardlinkRepairPreview)
async def hardlink_repair_preview(media_id: int, session: Session = Depends(get_session)) -> HardlinkRepairPreview:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    settings = session.get(Settings, 1)
    if settings is None or not (settings.qbittorrent_url and settings.qbittorrent_username and settings.qbittorrent_password):
        raise HTTPException(400, "qBittorrent non configuré.")
    try:
        return await build_repair_preview(session, media, settings)
    except QbittorrentAuthError as exc:
        raise HTTPException(502, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"qBittorrent injoignable : {exc}") from exc


@router.post("/{media_id}/hardlink-repair/execute", response_model=HardlinkRepairResult)
async def hardlink_repair_execute(media_id: int, session: Session = Depends(get_session)) -> HardlinkRepairResult:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    settings = session.get(Settings, 1)
    if settings is None or not (settings.qbittorrent_url and settings.qbittorrent_username and settings.qbittorrent_password):
        raise HTTPException(400, "qBittorrent non configuré.")
    try:
        return await execute_repair(session, media, settings)
    except QbittorrentAuthError as exc:
        raise HTTPException(502, str(exc)) from exc
