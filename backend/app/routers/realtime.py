"""État du temps réel et webhooks Sonarr/Radarr (voir services/realtime/).
Le temps réel est la norme, sans réglage : ces routes servent l'indicateur
de l'en-tête, l'état des webhooks dans les cartes Sonarr/Radarr et l'adresse
d'Analysarr (Réglages → Application). Routes authentifiées ; la réception
des webhooks est à part (routers/webhooks.py)."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app.database import engine, get_session
from app.models.settings import Settings
from app.schemas.realtime import (
    AddressWrite,
    ArrService,
    RealtimeStatusRead,
    SourceStatusRead,
    WebhookRead,
    WebhooksRead,
    WebhookState,
)
from app.services.arr_instances import ArrTarget, arr_target_by_id
from app.services.realtime import webhooks
from app.services.realtime.status import board
from app.services.realtime.supervisor import supervisor

router = APIRouter()


def _settings_or_400(session: Session) -> Settings:
    settings = session.get(Settings, 1)
    if settings is None:
        raise HTTPException(400, "Configuration manquante.")
    return settings


def _webhook_state(wanted: webhooks.WantedWebhook, connected: bool, base_url: str) -> tuple[WebhookState, str | None]:
    if connected:
        return "connected", None
    if not base_url:
        return "no_address", None
    status = board.get(wanted.key)
    if status is not None and status.state == "error":
        return "error", status.error
    return "pending", None


def _read(session: Session) -> WebhooksRead:
    settings = _settings_or_400(session)
    base_url = settings.analysarr_url
    hooks = []
    for wanted in webhooks.wanted_webhooks(session, settings):
        row = webhooks.find_row(session, wanted.target.kind, wanted.instance_id)
        connected = webhooks.is_connected(row, wanted.target, base_url, wanted.instance_id)
        state, error = _webhook_state(wanted, connected, base_url)
        hooks.append(
            WebhookRead(
                service="sonarr" if wanted.target.kind == "sonarr" else "radarr",
                instance_id=wanted.instance_id,
                name=wanted.target.name,
                state=state,
                error=error,
                url=row.url if row is not None and connected else None,
            )
        )
    return WebhooksRead(analysarr_url=base_url, webhooks=hooks)


def _read_fresh() -> WebhooksRead:
    with Session(engine) as session:
        return _read(session)


@router.get("/webhooks", response_model=WebhooksRead)
async def read_webhooks() -> WebhooksRead:
    return await run_in_threadpool(_read_fresh)


def _save_address(payload: AddressWrite) -> bool:
    """Vrai si l'adresse a changé. Une adresse détectée par le navigateur ne
    remplace jamais une adresse déjà enregistrée."""
    try:
        url = webhooks.normalize_base_url(payload.analysarr_url)
    except webhooks.WebhookError as exc:
        raise HTTPException(422, str(exc)) from exc
    with Session(engine) as session:
        settings = _settings_or_400(session)
        if (payload.detected and settings.analysarr_url) or settings.analysarr_url == url:
            return False
        settings.analysarr_url = url
        session.add(settings)
        session.commit()
        return True


@router.put("/address", response_model=WebhooksRead)
async def update_address(payload: AddressWrite) -> WebhooksRead:
    """Une nouvelle adresse rebranche tous les webhooks (mis à jour chez
    Sonarr/Radarr, jamais dupliqués)."""
    if await run_in_threadpool(_save_address, payload):
        await supervisor.apply(retry_webhooks=True)
    return await run_in_threadpool(_read_fresh)


def _status() -> RealtimeStatusRead:
    """Lu en permanence par l'en-tête : jamais d'erreur, même avant la
    configuration."""
    with Session(engine) as session:
        settings = session.get(Settings, 1)
    sources = [SourceStatusRead(**asdict(status)) for status in board.snapshot()]
    return RealtimeStatusRead(
        active=bool(sources),
        address_set=bool(settings and settings.analysarr_url),
        sources=sources,
    )


@router.get("/status", response_model=RealtimeStatusRead)
async def read_status() -> RealtimeStatusRead:
    return await run_in_threadpool(_status)


# --- Webhooks Sonarr/Radarr ---------------------------------------------------------


def _target(session: Session, service: ArrService, instance_id: int) -> ArrTarget:
    target = arr_target_by_id(session, _settings_or_400(session), service, instance_id or None)
    if target is None:
        raise HTTPException(404, "Instance Sonarr/Radarr introuvable.")
    return target


@router.post("/webhooks/{service}/{instance_id}", response_model=WebhooksRead)
async def retry_webhook(
    service: ArrService, instance_id: int, session: Session = Depends(get_session)
) -> WebhooksRead:
    """Réessaie tout de suite (sans attendre le prochain essai automatique)."""
    _target(session, service, instance_id)
    if not _settings_or_400(session).analysarr_url:
        raise HTTPException(409, "Adresse d'Analysarr inconnue : Réglages → Application.")
    await supervisor.apply(retry_webhooks=True)
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


@router.delete("/webhooks/{service}/{instance_id}", status_code=204)
async def unregister_webhook(service: ArrService, instance_id: int, session: Session = Depends(get_session)) -> None:
    """Appelé par l'interface juste avant de retirer une instance : le webhook
    disparaît aussi chez elle (sinon il resterait, en échec, dans Sonarr/Radarr)."""
    row = webhooks.find_row(session, service, instance_id)
    if row is None:
        return
    target = arr_target_by_id(session, _settings_or_400(session), service, instance_id or None)
    try:
        await webhooks.unregister(session, target, row)
    except webhooks.WebhookError as exc:
        raise HTTPException(502, str(exc)) from exc
