"""Règles d'automatisation : création, modification, exécution manuelle.

Chaque règle est créée par l'utilisateur, plafonnée et traçable — voir
services/automations.py pour les garde-fous."""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models.automation import Automation
from app.models.settings import Settings
from app.schemas.automations import (
    AutomationGuard,
    AutomationGuardWrite,
    AutomationRead,
    AutomationRunResult,
    AutomationWrite,
)
from app.services.automation_guard import (
    MIN_THRESHOLD_PERCENT,
    is_paused,
    paused_reason,
    resume_automations,
    threshold_percent,
    watched_statuses,
)
from app.services.automations import TRIGGER_STATUSES, eligible_medias, as_rule, rule_conditions, run_rule
from app.services.notifications import channel_targets

router = APIRouter()

MAX_AUTOMATIONS = 20


def _to_read(automation: Automation) -> AutomationRead:
    return AutomationRead(
        id=automation.id,
        name=automation.name,
        enabled=automation.enabled,
        trigger=automation.trigger,
        action=automation.action,
        conditions=rule_conditions(automation),
        max_actions=automation.max_actions,
        dry_run=automation.dry_run,
        last_run_at=automation.last_run_at,
        last_run_count=automation.last_run_count,
    )


def _guard(session: Session, settings: Settings | None) -> AutomationGuard:
    reason = paused_reason(settings) or {}
    return AutomationGuard(
        percent=threshold_percent(settings),
        min_percent=MIN_THRESHOLD_PERCENT,
        active=bool(watched_statuses(session)),
        paused=is_paused(settings),
        paused_at=settings.automations_paused_at if settings else None,
        status=reason.get("status"),
        previous=reason.get("previous"),
        current=reason.get("current"),
        total=reason.get("total"),
        changed_percent=reason.get("percent"),
    )


def _settings(session: Session) -> Settings:
    settings = session.get(Settings, 1)
    if settings is None:
        raise HTTPException(400, "Configuration manquante.")
    return settings


def _get(automation_id: int, session: Session) -> Automation:
    automation = session.get(Automation, automation_id)
    if automation is None:
        raise HTTPException(404, "Automatisation introuvable.")
    return automation


def _apply(automation: Automation, payload: AutomationWrite) -> None:
    if not payload.name.strip():
        raise HTTPException(400, "Chaque automatisation doit avoir un nom.")
    if payload.action == "repair_hardlinks" and payload.trigger != "non_hardlink_detected":
        raise HTTPException(400, "La réparation des hardlinks ne s'applique qu'aux torrents non hardlinkés.")
    automation.name = payload.name.strip()
    automation.enabled = payload.enabled
    automation.trigger = payload.trigger
    automation.action = payload.action
    automation.conditions = json.dumps(payload.conditions.model_dump(exclude_none=True))
    automation.max_actions = payload.max_actions
    automation.dry_run = payload.dry_run


@router.get("/guard", response_model=AutomationGuard)
def read_guard(session: Session = Depends(get_session)) -> AutomationGuard:
    return _guard(session, session.get(Settings, 1))


@router.put("/guard", response_model=AutomationGuard)
def update_guard(payload: AutomationGuardWrite, session: Session = Depends(get_session)) -> AutomationGuard:
    settings = _settings(session)
    settings.automation_guard_percent = payload.percent
    session.add(settings)
    session.commit()
    return _guard(session, settings)


@router.post("/guard/resume", response_model=AutomationGuard)
def resume_guard(session: Session = Depends(get_session)) -> AutomationGuard:
    """Reprise manuelle après une mise en pause : c'est l'utilisateur qui
    confirme que le basculement était légitime (voir automation_guard.py)."""
    settings = _settings(session)
    resume_automations(session, settings)
    return _guard(session, settings)


@router.get("", response_model=list[AutomationRead])
def list_automations(session: Session = Depends(get_session)) -> list[AutomationRead]:
    automations = session.exec(select(Automation).order_by(Automation.id)).all()
    return [_to_read(a) for a in automations]


@router.post("", response_model=AutomationRead, status_code=201)
def create_automation(payload: AutomationWrite, session: Session = Depends(get_session)) -> AutomationRead:
    if len(session.exec(select(Automation.id)).all()) >= MAX_AUTOMATIONS:
        raise HTTPException(400, f"{MAX_AUTOMATIONS} automatisations maximum.")
    automation = Automation(name="", trigger=payload.trigger, action=payload.action)
    _apply(automation, payload)
    session.add(automation)
    session.commit()
    session.refresh(automation)
    return _to_read(automation)


@router.put("/{automation_id}", response_model=AutomationRead)
def update_automation(
    automation_id: int, payload: AutomationWrite, session: Session = Depends(get_session)
) -> AutomationRead:
    automation = _get(automation_id, session)
    _apply(automation, payload)
    session.add(automation)
    session.commit()
    session.refresh(automation)
    return _to_read(automation)


@router.delete("/{automation_id}", status_code=204)
def delete_automation(automation_id: int, session: Session = Depends(get_session)) -> None:
    session.delete(_get(automation_id, session))
    session.commit()


@router.get("/{automation_id}/preview", response_model=AutomationRunResult)
def preview_automation(automation_id: int, session: Session = Depends(get_session)) -> AutomationRunResult:
    """Ce que la règle ferait maintenant, sans rien exécuter."""
    automation = _get(automation_id, session)
    rule = as_rule(automation)
    eligible = eligible_medias(session, rule)
    return AutomationRunResult(
        automation_id=rule.id,
        name=rule.name,
        matched=len(eligible),
        executed=0,
        dry_run=True,
        freed_bytes=sum(media.reclaimable_bytes for media, _ in eligible[: rule.max_actions]),
        steps=[{"label": media.title, "success": True} for media, _ in eligible[: rule.max_actions]],
    )


@router.post("/{automation_id}/run", response_model=AutomationRunResult)
async def run_automation(automation_id: int, session: Session = Depends(get_session)) -> AutomationRunResult:
    """Exécution manuelle immédiate (les règles activées tournent aussi après
    chaque scan)."""
    automation = _get(automation_id, session)
    settings = session.get(Settings, 1)
    if settings is None:
        raise HTTPException(400, "Configuration manquante.")
    if automation.trigger not in TRIGGER_STATUSES:
        raise HTTPException(400, "Déclencheur inconnu.")
    return await run_rule(session, settings, channel_targets(session), automation)
