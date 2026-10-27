"""État du temps réel et webhooks Sonarr/Radarr (voir services/realtime/).
Le temps réel est actif par défaut (interrupteur dans Réglages →
Planification, enregistré avec les réglages) : ces routes servent
l'indicateur de l'en-tête, l'état des webhooks dans les cartes Sonarr/Radarr
(et leur interrupteur) et l'adresse d'Analysarr (Réglages → Application).
Routes authentifiées ; la réception des webhooks est à part
(routers/webhooks.py). Aucune n'attend Sonarr/Radarr, sauf « Réessayer »,
geste explicite."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app.database import engine, get_session
from app.models.arr_webhook import ArrWebhook
from app.models.settings import Settings
from app.schemas.realtime import (
    AddressWrite,
    ArrService,
    RealtimeStatusRead,
    SourceStatusRead,
    WebhookRead,
    WebhooksEnabledWrite,
    WebhooksRead,
    WebhookState,
)
from app.services.arr_instances import ArrTarget, arr_target_by_id
from app.services.realtime import webhooks
from app.services.realtime.status import board
from app.services.realtime.supervisor import supervisor
from app.services.realtime.watchers import arr_source_key

router = APIRouter()


def _settings_or_400(session: Session) -> Settings:
    settings = session.get(Settings, 1)
    if settings is None:
        raise HTTPException(400, "Configuration manquante.")
    return settings


def _webhook_state(
    wanted: webhooks.WantedWebhook, row: ArrWebhook | None, settings: Settings
) -> tuple[WebhookState, str | None]:
    status = board.get(wanted.key)
    error = status.error if status is not None and status.state == "error" else None
    if not (settings.realtime_enabled and settings.arr_webhooks_enabled):
        if error:
            return "error", error  # retrait refusé
        return ("removing", None) if row is not None else ("disabled", None)
    if webhooks.is_connected(row, wanted.target, settings.analysarr_url, wanted.instance_id):
        return "connected", None
    if not settings.analysarr_url:
        return "no_address", None
    return ("error", error) if error else ("pending", None)


def _read(session: Session) -> WebhooksRead:
    settings = _settings_or_400(session)
    hooks = []
    for wanted in webhooks.wanted_webhooks(session, settings):
        row = webhooks.find_row(session, wanted.target.kind, wanted.instance_id)
        state, error = _webhook_state(wanted, row, settings)
        polling = board.get(arr_source_key(wanted.target.kind, wanted.instance_id))
        hooks.append(
            WebhookRead(
                service="sonarr" if wanted.target.kind == "sonarr" else "radarr",
                instance_id=wanted.instance_id,
                name=wanted.target.name,
                state=state,
                error=error,
                url=row.url if row is not None and state == "connected" else None,
                polling=polling.state if polling is not None else None,
                polling_error=polling.error if polling is not None else None,
            )
        )
    return WebhooksRead(
        analysarr_url=settings.analysarr_url,
        realtime_enabled=settings.realtime_enabled,
        enabled=settings.arr_webhooks_enabled,
        webhooks=hooks,
    )


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
    Sonarr/Radarr, jamais dupliqués), en tâche de fond."""
    if await run_in_threadpool(_save_address, payload):
        supervisor.request_webhooks(retry=True)
    return await run_in_threadpool(_read_fresh)


def _save_webhooks_enabled(enabled: bool) -> None:
    with Session(engine) as session:
        settings = _settings_or_400(session)
        settings.arr_webhooks_enabled = enabled
        session.add(settings)
        session.commit()


@router.put("/webhooks/enabled", response_model=WebhooksRead)
async def set_webhooks_enabled(payload: WebhooksEnabledWrite) -> WebhooksRead:
    """Autorise (ou retire) la création automatique des webhooks chez
    Sonarr/Radarr. Désactivés, ceux d'Analysarr sont retirés de chez eux en
    tâche de fond ; le suivi en lecture seule continue."""
    await run_in_threadpool(_save_webhooks_enabled, payload.enabled)
    supervisor.request_webhooks(retry=True)
    return await run_in_threadpool(_read_fresh)


def _status() -> RealtimeStatusRead:
    """Lu en permanence par l'en-tête : jamais d'erreur, même avant la
    configuration."""
    with Session(engine) as session:
        settings = session.get(Settings, 1)
    enabled = settings is None or settings.realtime_enabled
    sources = [SourceStatusRead(**asdict(status)) for status in board.snapshot()] if enabled else []
    return RealtimeStatusRead(
        enabled=enabled,
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
    """Réessaie tout de suite (sans attendre le prochain essai automatique) :
    branchement, ou retrait si les webhooks sont désactivés. Attend le
    résultat : c'est un geste explicite."""
    _target(session, service, instance_id)
    settings = _settings_or_400(session)
    if settings.realtime_enabled and settings.arr_webhooks_enabled and not settings.analysarr_url:
        raise HTTPException(409, "Adresse d'Analysarr inconnue : Réglages → Application.")
    await supervisor.reconcile_webhooks(retry=True)
    session.expire_all()
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
