import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session, select

from app.clients.emby import EmbyClient
from app.database import get_session
from app.models.media import Media, MediaFile, Torrent
from app.models.settings import Settings
from app.schemas.media import (
    CrossSeedSearchResult,
    DeleteExecuteResult,
    DeletePreview,
    MediaDetail,
    MediaFileRead,
    MediaListItem,
    MediaListResponse,
    TorrentRead,
    TrackerRead,
)
from app.services.cascade_delete import build_delete_preview, execute_delete, trigger_cross_seed_search

router = APIRouter()


def _to_list_item(media: Media) -> MediaListItem:
    return MediaListItem(
        id=media.id,
        media_type=media.media_type.value,
        title=media.title,
        year=media.year,
        statuses=[s for s in media.statuses.split(",") if s],
        reclaimable_bytes=media.reclaimable_bytes,
        has_poster=media.has_poster,
        last_scanned_at=media.last_scanned_at,
    )


@router.get("", response_model=MediaListResponse)
def list_media(
    status: Optional[str] = Query(None, description="doublon | orphelin_qbit | tracker_unique | sain"),
    media_type: Optional[str] = Query(None, description="movie | series"),
    search: Optional[str] = None,
    sort: str = Query("title", description="title | year | size"),
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

    if sort == "year":
        medias.sort(key=lambda m: m.year or 0, reverse=True)
    elif sort == "size":
        medias.sort(key=lambda m: m.reclaimable_bytes, reverse=True)
    else:
        medias.sort(key=lambda m: m.title.lower())

    return MediaListResponse(items=[_to_list_item(m) for m in medias], total=len(medias))


@router.get("/{media_id}", response_model=MediaDetail)
def get_media(media_id: int, session: Session = Depends(get_session)) -> MediaDetail:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")

    files = session.exec(select(MediaFile).where(MediaFile.media_id == media_id)).all()
    torrents = session.exec(select(Torrent).where(Torrent.media_id == media_id)).all()

    return MediaDetail(
        **_to_list_item(media).model_dump(),
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
                is_hardlinked=t.is_hardlinked,
                trackers=[TrackerRead(**d) for d in json.loads(t.trackers_json)],
            )
            for t in torrents
        ],
    )


@router.get("/{media_id}/poster")
async def get_poster(media_id: int, session: Session = Depends(get_session)) -> Response:
    media = session.get(Media, media_id)
    if media is None or not media.emby_item_id:
        raise HTTPException(404, "Pas de jaquette disponible.")
    settings = session.get(Settings, 1)
    if settings is None or not settings.emby_url or not settings.emby_api_key:
        raise HTTPException(404, "Emby non configuré.")

    emby = EmbyClient(settings.emby_url, settings.emby_api_key)
    result = await emby.fetch_poster(media.emby_item_id)
    if result is None:
        raise HTTPException(404, "Jaquette introuvable.")
    content, content_type = result
    return Response(content=content, media_type=content_type)


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


@router.post("/{media_id}/cross-seed-search", response_model=CrossSeedSearchResult)
async def cross_seed_search(media_id: int, session: Session = Depends(get_session)) -> CrossSeedSearchResult:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    settings = session.get(Settings, 1)
    if settings is None:
        raise HTTPException(400, "Configuration manquante.")
    torrents = session.exec(select(Torrent).where(Torrent.media_id == media_id)).all()
    return await trigger_cross_seed_search(settings, [t.hash for t in torrents])
