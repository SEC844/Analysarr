import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.database import engine, get_session
from app.models.media import ScanRun
from app.models.settings import Settings
from app.clients.qbittorrent import QbittorrentAuthError
from app.schemas.diagnostics import DiagnosticsResult, EmbyFileDebug, TorrentDebug
from app.schemas.media import ScanRunRead
from app.services.diagnostics import debug_emby_series_files, debug_torrents, run_diagnostics
from app.services.events import scan_events
from app.services.scan import is_scan_running, run_scan

router = APIRouter()


def _to_read(run: ScanRun) -> ScanRunRead:
    return ScanRunRead(
        id=run.id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status.value,
        error_message=run.error_message,
        media_count=run.media_count,
        duplicate_count=run.duplicate_count,
        orphan_count=run.orphan_count,
        tracker_unique_count=run.tracker_unique_count,
        qbittorrent_torrent_count=run.qbittorrent_torrent_count,
        qbittorrent_matched_count=run.qbittorrent_matched_count,
    )


@router.post("", status_code=202)
async def start_scan() -> dict:
    if is_scan_running():
        return {"started": False, "message": "Un scan est déjà en cours."}
    asyncio.create_task(run_scan())
    return {"started": True}


@router.get("/status", response_model=Optional[ScanRunRead])
def scan_status() -> Optional[ScanRunRead]:
    with Session(engine) as session:
        run = session.exec(select(ScanRun).order_by(ScanRun.started_at.desc())).first()
        return _to_read(run) if run else None


@router.get("/stream")
async def scan_stream() -> StreamingResponse:
    queue = scan_events.subscribe()

    async def gen():
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("completed", "failed"):
                    break
        finally:
            scan_events.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/diagnostics", response_model=DiagnosticsResult)
async def scan_diagnostics(session: Session = Depends(get_session)) -> DiagnosticsResult:
    settings = session.get(Settings, 1)
    if settings is None or not (settings.emby_url and settings.emby_api_key):
        raise HTTPException(400, "Emby non configuré.")
    if not (settings.qbittorrent_url and settings.qbittorrent_username and settings.qbittorrent_password):
        raise HTTPException(400, "qBittorrent non configuré.")
    try:
        return await run_diagnostics(settings)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/debug/torrents", response_model=list[TorrentDebug])
async def scan_debug_torrents(
    name_contains: str, session: Session = Depends(get_session)
) -> list[TorrentDebug]:
    """Diagnostic ponctuel (pas d'UI dédiée) : détaille le rattachement par
    inode fichier par fichier pour les torrents qBittorrent dont le nom
    contient `name_contains`. Utile pour comprendre pourquoi un torrent connu
    de qBittorrent n'apparaît sur aucune fiche média."""
    settings = session.get(Settings, 1)
    if settings is None or not (settings.qbittorrent_url and settings.qbittorrent_username and settings.qbittorrent_password):
        raise HTTPException(400, "qBittorrent non configuré.")
    try:
        return await debug_torrents(settings, name_contains)
    except QbittorrentAuthError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/debug/emby-series", response_model=list[EmbyFileDebug])
async def scan_debug_emby_series(
    title_contains: str, session: Session = Depends(get_session)
) -> list[EmbyFileDebug]:
    """Diagnostic ponctuel : détaille le chemin et l'inode réels de chaque
    fichier d'épisode pour les séries Emby dont le titre contient
    `title_contains`. À comparer avec /debug/torrents pour trouver quel
    torrent est réellement hardlinké au fichier actif."""
    settings = session.get(Settings, 1)
    if settings is None or not (settings.emby_url and settings.emby_api_key):
        raise HTTPException(400, "Emby non configuré.")
    return await debug_emby_series_files(settings, title_contains)
