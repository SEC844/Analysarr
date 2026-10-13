"""Canaux de notification : plusieurs par type, chacun avec ses événements.

Secrets (URL de webhook Discord, sujet ntfy, jetons) : écrits seulement, jamais
renvoyés au navigateur — une mise à jour sans valeur conserve celle enregistrée,
comme pour les clés API des services."""

import json
from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, select
from starlette.concurrency import run_in_threadpool

from app.database import get_session
from app.models.ids import row_id
from app.models.notification_channel import NotificationChannel
from app.models.settings import Settings
from app.schemas.notifications import (
    ChannelKind,
    ChannelRead,
    ChannelTestResult,
    ChannelWrite,
    SummaryChannelResult,
    WeeklySummaryResult,
)
from app.services.notifications import (
    MAX_CHANNELS,
    NOTIFICATION_EVENTS,
    ChannelTarget,
    build_test_notification,
    channel_events,
    is_discord_webhook,
    is_http_url,
    notification_language,
    send,
)
from app.services.scheduler import refresh_update_watch
from app.services.weekly_summary import send_weekly_summary

router = APIRouter()


def _to_read(channel: NotificationChannel) -> ChannelRead:
    return ChannelRead(
        id=row_id(channel),
        kind=cast(ChannelKind, channel.kind),  # validé par ChannelWrite
        name=channel.name,
        enabled=channel.enabled,
        events=list(channel_events(channel)),
        url=channel.url if channel.kind == "gotify" else None,
        url_set=bool(channel.url),
        token_set=bool(channel.token),
    )


def _validate(payload: ChannelWrite, url: str, token: str | None) -> None:
    if not payload.name.strip():
        raise HTTPException(400, "Chaque canal doit avoir un nom.")
    if payload.kind == "discord":
        if not is_discord_webhook(url):
            raise HTTPException(400, "L'URL Discord doit être une URL de webhook Discord (https://discord.com/api/webhooks/…).")
    elif not is_http_url(url):
        raise HTTPException(400, "L'URL doit commencer par http:// ou https://.")
    if payload.kind == "gotify" and not token:
        raise HTTPException(400, "Gotify exige un jeton d'application.")
    unknown = [event for event in payload.events if event not in NOTIFICATION_EVENTS]
    if unknown:
        raise HTTPException(400, f"Événement inconnu : {', '.join(unknown)}.")


def _get(channel_id: int, session: Session) -> NotificationChannel:
    channel = session.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(404, "Canal introuvable.")
    return channel


@router.get("/channels", response_model=list[ChannelRead])
def list_channels(session: Session = Depends(get_session)) -> list[ChannelRead]:
    channels = session.exec(select(NotificationChannel).order_by(col(NotificationChannel.id))).all()
    return [_to_read(channel) for channel in channels]


@router.post("/channels", response_model=ChannelRead, status_code=201)
def create_channel(payload: ChannelWrite, session: Session = Depends(get_session)) -> ChannelRead:
    if len(session.exec(select(NotificationChannel.id)).all()) >= MAX_CHANNELS:
        raise HTTPException(400, f"{MAX_CHANNELS} canaux maximum.")
    url = (payload.url or "").strip()
    if not url:
        raise HTTPException(400, "L'adresse du canal est requise.")
    _validate(payload, url, payload.token)

    channel = NotificationChannel(
        kind=payload.kind,
        name=payload.name.strip(),
        url=url,
        token=payload.token or None,
        events=json.dumps(sorted(set(payload.events))),
        enabled=payload.enabled,
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)
    refresh_update_watch(session)
    return _to_read(channel)


@router.put("/channels/{channel_id}", response_model=ChannelRead)
def update_channel(channel_id: int, payload: ChannelWrite, session: Session = Depends(get_session)) -> ChannelRead:
    channel = _get(channel_id, session)
    if payload.kind != channel.kind:
        raise HTTPException(400, "Le type d'un canal ne peut pas changer : créez-en un nouveau.")
    # Secrets : une valeur vide conserve celle enregistrée (le navigateur ne la
    # reçoit jamais, « vide » veut dire « non ressaisie »).
    url = (payload.url or "").strip() or channel.url
    token = payload.token or channel.token
    _validate(payload, url, token)

    channel.name = payload.name.strip()
    channel.url = url
    channel.token = token
    channel.events = json.dumps(sorted(set(payload.events)))
    channel.enabled = payload.enabled
    session.add(channel)
    session.commit()
    session.refresh(channel)
    refresh_update_watch(session)
    return _to_read(channel)


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: int, session: Session = Depends(get_session)) -> None:
    session.delete(_get(channel_id, session))
    session.commit()
    refresh_update_watch(session)


@router.post("/channels/{channel_id}/test", response_model=ChannelTestResult)
async def test_channel(channel_id: int, session: Session = Depends(get_session)) -> ChannelTestResult:
    """Envoie une notification de test sur ce canal ENREGISTRÉ (jamais sur une
    adresse fournie dans la requête : pas de relais vers une adresse arbitraire)."""
    target, language = await run_in_threadpool(_test_target, channel_id, session)
    results = await send([target], build_test_notification(language))
    error = results.get(target.name)
    return ChannelTestResult(ok=error is None, error=error)


def _test_target(channel_id: int, session: Session) -> tuple[ChannelTarget, str]:
    channel = _get(channel_id, session)
    target = ChannelTarget(
        id=row_id(channel),
        kind=channel.kind,
        name=channel.name,
        url=channel.url,
        token=channel.token,
        events=channel_events(channel),
    )
    return target, notification_language(session.get(Settings, 1))


@router.post("/weekly-summary", response_model=WeeklySummaryResult)
async def send_weekly_summary_now() -> WeeklySummaryResult:
    """Envoie le résumé hebdomadaire tout de suite, aux seuls canaux abonnés
    (pour le voir sans attendre lundi)."""
    delivery = await send_weekly_summary()
    return WeeklySummaryResult(
        channels=[SummaryChannelResult(name=name, error=error) for name, error in delivery.results.items()],
        skipped=delivery.skipped,
    )
