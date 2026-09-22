import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.database import engine, get_session
from app.models.media import ScanRun
from app.models.settings import Settings
from app.clients.torrent import TorrentAuthError, torrent_client_configured, torrent_client_name
from app.schemas.diagnostics import DiagnosticsResult, UnmatchedTorrent
from app.schemas.media import ScanRunRead
from app.services.diagnostics import (
    list_unmatched_torrents,
    run_diagnostics,
)
from app.services.events import scan_events
from app.services.scan import is_scan_running, launch_scan
from app.services.scan_scopes import SCAN_SCOPES

router = APIRouter()


# Ping SSE (commentaire ignoré par EventSource) : garde la connexion vivante
# derrière un reverse-proxy qui coupe les connexions inactives.
STREAM_HEARTBEAT_SECONDS = 15


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
        trigger=run.trigger,
        scope=run.scope,
    )


@router.post("", status_code=202)
async def start_scan(scope: str = Query("full", description=" | ".join(SCAN_SCOPES))) -> dict:
    """Lance une analyse. `scope` limite le périmètre : une valeur inconnue est
    refusée plutôt qu'interprétée (liste fermée, jamais de texte libre)."""
    if scope not in SCAN_SCOPES:
        raise HTTPException(400, f"Périmètre inconnu : {', '.join(SCAN_SCOPES)}.")
    if is_scan_running():
        return {"started": False, "message": "Un scan est déjà en cours."}
    return {"started": launch_scan(scope=scope)}


@router.get("/status", response_model=Optional[ScanRunRead])
def scan_status() -> Optional[ScanRunRead]:
    with Session(engine) as session:
        run = session.exec(select(ScanRun).order_by(ScanRun.started_at.desc())).first()
        return _to_read(run) if run else None


@router.get("/history", response_model=list[ScanRunRead])
def scan_history(limit: int = 50, session: Session = Depends(get_session)) -> list[ScanRunRead]:
    runs = session.exec(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(limit)).all()
    return [_to_read(r) for r in runs]


@router.get("/stream")
async def scan_stream() -> StreamingResponse:
    queue = scan_events.subscribe()

    async def gen():
        try:
            # Premier octet immédiat : le frontend attend l'ouverture du flux
            # (`onopen`) pour lancer le scan. Sans rien à envoyer avant le
            # premier événement, un reverse-proxy qui met la réponse en tampon
            # ne transmet jamais l'ouverture — l'interface restait bloquée sur
            # « Démarrage du scan... » sans que le scan ne parte.
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=STREAM_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("completed", "failed"):
                    break
        finally:
            scan_events.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        # no-transform + X-Accel-Buffering : ni compression ni mise en tampon
        # par un proxy (nginx, Nginx Proxy Manager, SWAG...).
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/diagnostics", response_model=DiagnosticsResult)
async def scan_diagnostics(session: Session = Depends(get_session)) -> DiagnosticsResult:
    settings = session.get(Settings, 1)
    if settings is None or not (settings.emby_url and settings.emby_api_key):
        raise HTTPException(400, "Serveur multimédia non configuré.")
    if not torrent_client_configured(settings):
        raise HTTPException(400, f"{torrent_client_name(settings)} non configuré.")
    try:
        return await run_diagnostics(settings)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/debug/unmatched-torrents", response_model=list[UnmatchedTorrent])
async def scan_debug_unmatched_torrents(session: Session = Depends(get_session)) -> list[UnmatchedTorrent]:
    """Diagnostic ponctuel : liste les torrents qBittorrent qui n'ont été
    rattachés à AUCUN média lors du dernier scan (complément exact de
    qbittorrent_matched_count / qbittorrent_torrent_count) — pour savoir
    concrètement lesquels échappent au rattachement plutôt que de se fier
    seulement au chiffre agrégé."""
    settings = session.get(Settings, 1)
    if not torrent_client_configured(settings):
        raise HTTPException(400, f"{torrent_client_name(settings)} non configuré.")
    try:
        return await list_unmatched_torrents(session, settings)
    except TorrentAuthError as exc:
        raise HTTPException(502, str(exc)) from exc
