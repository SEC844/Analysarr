"""Réglages et état du temps réel (voir services/realtime/). Routes
authentifiées ; la réception des webhooks est à part (routers/webhooks.py)."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, select
from starlette.concurrency import run_in_threadpool

from app.clients.emby import media_server_client
from app.clients.torrent import torrent_client_configured
from app.database import engine, get_session
from app.models.arr_webhook import ArrWebhook
from app.models.settings import Settings
from app.schemas.realtime import (
    ArrService,
    NightlyScanRequest,
    RealtimeSettingsRead,
    RealtimeSettingsWrite,
    RealtimeStatusRead,
    ReconciliationRead,
    SourceStatusRead,
    WebhookPreviewRead,
    WebhookRead,
    WebhookRequest,
)
from app.services.arr_instances import ArrTarget, arr_target_by_id, arr_targets
from app.services.realtime import webhooks
from app.services.realtime.status import board
from app.services.realtime.supervisor import supervisor
from app.services.scheduler import configure_scan_schedule_from

router = APIRouter()


def _settings_or_404(session: Session) -> Settings:
    settings = session.get(Settings, 1)
    if settings is None:
        raise HTTPException(400, "Configuration manquante.")
    return settings


def _read(session: Session) -> RealtimeSettingsRead:
    settings = _settings_or_404(session)
    rows = {(r.service, r.instance_id): r for r in session.exec(select(ArrWebhook)).all()}
    hooks = []
    for service in webhooks.ARR_SERVICES:
        for target in arr_targets(session, settings, service):
            instance_id = target.instance_id or 0
            row = rows.get((service, instance_id))
            connected = row is not None and row.notification_id is not None
            hooks.append(
                WebhookRead(
                    service=service,
                    instance_id=instance_id,
                    name=target.name,
                    connected=connected,
                    url=row.url if row is not None and connected else None,
                )
            )
    return RealtimeSettingsRead(
        debounce_seconds=settings.realtime_debounce_seconds,
        torrents_enabled=settings.realtime_torrents_enabled,
        torrents_interval=settings.realtime_torrents_interval,
        torrents_available=torrent_client_configured(settings),
        media_server_enabled=settings.realtime_media_server_enabled,
        media_server_interval=settings.realtime_media_server_interval,
        media_server_available=media_server_client(settings) is not None,
        analysarr_url=settings.analysarr_url,
        webhooks=hooks,
    )


def _read_fresh() -> RealtimeSettingsRead:
    with Session(engine) as session:
        return _read(session)


@router.get("/settings", response_model=RealtimeSettingsRead)
def read_settings(session: Session = Depends(get_session)) -> RealtimeSettingsRead:
    return _read(session)


def _save(payload: RealtimeSettingsWrite) -> None:
    with Session(engine) as session:
        settings = _settings_or_404(session)
        settings.realtime_debounce_seconds = payload.debounce_seconds
        settings.realtime_torrents_enabled = payload.torrents_enabled
        settings.realtime_torrents_interval = payload.torrents_interval
        settings.realtime_media_server_enabled = payload.media_server_enabled
        settings.realtime_media_server_interval = payload.media_server_interval
        session.add(settings)
        session.commit()


@router.put("/settings", response_model=RealtimeSettingsRead)
async def update_settings(payload: RealtimeSettingsWrite) -> RealtimeSettingsRead:
    await run_in_threadpool(_save, payload)
    await supervisor.apply()
    return await run_in_threadpool(_read_fresh)


def _status() -> RealtimeStatusRead:
    """Lu en permanence par l'en-tête : jamais d'erreur, même avant la
    configuration (temps réel simplement inactif)."""
    with Session(engine) as session:
        settings = session.get(Settings, 1) or Settings(id=1)
        hooked = session.exec(select(ArrWebhook.id).where(col(ArrWebhook.notification_id).is_not(None))).first()
    reconciliation = ReconciliationRead(
        enabled=settings.scan_schedule_enabled,
        mode="nightly" if settings.scan_schedule_mode == "nightly" else "interval",
        interval_minutes=settings.scan_schedule_interval_minutes,
        nightly_hour=settings.scan_nightly_hour,
    )
    active = settings.realtime_torrents_enabled or settings.realtime_media_server_enabled or hooked is not None
    sources = [SourceStatusRead(**asdict(status)) for status in board.snapshot()]
    return RealtimeStatusRead(active=active, sources=sources, reconciliation=reconciliation)


@router.get("/status", response_model=RealtimeStatusRead)
async def read_status() -> RealtimeStatusRead:
    return await run_in_threadpool(_status)


# --- Webhooks Sonarr/Radarr ---------------------------------------------------------


def _target(session: Session, service: ArrService, instance_id: int) -> ArrTarget:
    target = arr_target_by_id(session, _settings_or_404(session), service, instance_id or None)
    if target is None:
        raise HTTPException(404, "Instance Sonarr/Radarr introuvable.")
    return target


def _base_url(payload: WebhookRequest) -> str:
    try:
        return webhooks.normalize_base_url(payload.analysarr_url)
    except webhooks.WebhookError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/webhooks/{service}/{instance_id}/preview", response_model=WebhookPreviewRead)
async def preview_webhook(
    service: ArrService, instance_id: int, payload: WebhookRequest, session: Session = Depends(get_session)
) -> WebhookPreviewRead:
    target = _target(session, service, instance_id)
    try:
        preview = await webhooks.preview(target, _base_url(payload), instance_id)
    except webhooks.WebhookError as exc:
        raise HTTPException(502, str(exc)) from exc
    return WebhookPreviewRead(name=preview.name, url=preview.url, events=preview.events)


@router.post("/webhooks/{service}/{instance_id}", response_model=RealtimeSettingsRead)
async def register_webhook(
    service: ArrService, instance_id: int, payload: WebhookRequest, session: Session = Depends(get_session)
) -> RealtimeSettingsRead:
    target = _target(session, service, instance_id)
    base_url = _base_url(payload)
    try:
        await webhooks.register(session, target, base_url, instance_id)
    except webhooks.WebhookError as exc:
        raise HTTPException(502, str(exc)) from exc
    settings = _settings_or_404(session)
    settings.analysarr_url = base_url
    session.add(settings)
    session.commit()
    await supervisor.apply()
    return _read(session)


@router.post("/webhooks/{service}/{instance_id}/test", status_code=204)
async def test_webhook(service: ArrService, instance_id: int, session: Session = Depends(get_session)) -> None:
    target = _target(session, service, instance_id)
    row = webhooks.find_row(session, service, instance_id)
    if row is None:
        raise HTTPException(404, "Webhook non branché.")
    try:
        await webhooks.send_test(target, row)
    except webhooks.WebhookError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.delete("/webhooks/{service}/{instance_id}", response_model=RealtimeSettingsRead)
async def unregister_webhook(
    service: ArrService, instance_id: int, session: Session = Depends(get_session)
) -> RealtimeSettingsRead:
    row = webhooks.find_row(session, service, instance_id)
    if row is None:
        raise HTTPException(404, "Webhook non branché.")
    target = arr_target_by_id(session, _settings_or_404(session), service, instance_id or None)
    try:
        await webhooks.unregister(session, target, row)
    except webhooks.WebhookError as exc:
        raise HTTPException(502, str(exc)) from exc
    await supervisor.apply()
    return _read(session)


# --- Scan complet de réconciliation -----------------------------------------------------


def _schedule_nightly(hour: int) -> None:
    with Session(engine) as session:
        settings = _settings_or_404(session)
        settings.scan_schedule_enabled = True
        settings.scan_schedule_mode = "nightly"
        settings.scan_nightly_hour = hour
        session.add(settings)
        session.commit()
        configure_scan_schedule_from(settings)


@router.post("/nightly-scan", response_model=RealtimeStatusRead)
async def use_nightly_scan(payload: NightlyScanRequest) -> RealtimeStatusRead:
    """Proposé dès qu'une source est branchée, jamais imposé : le scan complet
    devient un filet de sécurité, une fois par nuit."""
    await run_in_threadpool(_schedule_nightly, payload.hour)
    return await run_in_threadpool(_status)
