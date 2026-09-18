"""Automatisations : exécuter une action sur les médias qu'un scan vient de
classer (orphelins, doublons, non hardlinkés), sous conditions.

Garde-fous, parce qu'une automatisation supprime des fichiers sans que
personne ne regarde :
- rien ne tourne tant que l'utilisateur n'a pas créé de règle ;
- chaque règle ne traite qu'un nombre plafonné de médias par exécution ;
- le mode simulation liste ce qui serait fait sans rien exécuter ;
- les actions réutilisent exactement le code des boutons de l'interface
  (nettoyage cascade, réparation de hardlinks, recherche cross-seed) : un
  torrent protégé ou réparable n'est donc jamais supprimé ;
- chaque exécution est tracée dans l'historique et notifiable."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlmodel import Session, select

from app.models.automation import Automation
from app.models.media import Media, MediaFile, Torrent
from app.models.settings import Settings
from app.schemas.automations import AutomationConditions, AutomationRunResult, AutomationStep
from app.services.action_log import MediaRef, record_action
from app.services.notifications import ChannelTarget, automation_notification, notification_language, notify

# Statut (calculé au scan) qui rend un média éligible à chaque déclencheur.
TRIGGER_STATUSES = {
    "orphan_detected": "orphelin_qbit",
    "duplicate_detected": "doublon",
    "non_hardlink_detected": "non_hardlink",
}
MAX_ACTIONS_LIMIT = 50


@dataclass(frozen=True)
class AutomationRule:
    """Copie d'une règle : utilisable après la fermeture de la session."""

    id: int
    name: str
    trigger: str
    action: str
    conditions: AutomationConditions
    max_actions: int
    dry_run: bool


def rule_conditions(automation: Automation) -> AutomationConditions:
    try:
        return AutomationConditions.model_validate(json.loads(automation.conditions or "{}"))
    except (ValueError, TypeError):
        # Conditions illisibles : la règle ne filtre rien plutôt que d'échouer.
        return AutomationConditions()


def as_rule(automation: Automation) -> AutomationRule:
    return AutomationRule(
        id=automation.id,
        name=automation.name,
        trigger=automation.trigger,
        action=automation.action,
        conditions=rule_conditions(automation),
        max_actions=min(automation.max_actions, MAX_ACTIONS_LIMIT),
        dry_run=automation.dry_run,
    )


def _concerned_torrents(trigger: str, torrents: list[Torrent]) -> list[Torrent]:
    """Torrents sur lesquels portent les conditions : les orphelins pour un
    nettoyage, les non hardlinkés pour une réparation."""
    if trigger == "orphan_detected":
        return [t for t in torrents if t.is_hardlinked is False and not t.repairable]
    if trigger == "non_hardlink_detected":
        return [t for t in torrents if t.is_hardlinked is False and t.repairable]
    return list(torrents)


def _seeded_days(torrent: Torrent, now: datetime) -> float | None:
    reference = torrent.completed_on or torrent.added_on
    if reference is None:
        return None
    return (now - reference.replace(tzinfo=timezone.utc)).total_seconds() / 86400


def matches(rule: AutomationRule, media: Media, torrents: list[Torrent], now: datetime) -> bool:
    conditions = rule.conditions
    if conditions.media_types and media.media_type.value not in conditions.media_types:
        return False
    if conditions.min_reclaimable_bytes and media.reclaimable_bytes < conditions.min_reclaimable_bytes:
        return False

    concerned = _concerned_torrents(rule.trigger, torrents)
    if conditions.min_ratio is not None:
        if not concerned or any((t.ratio or 0) < conditions.min_ratio for t in concerned):
            return False
    if conditions.min_seed_days is not None:
        if not concerned:
            return False
        for torrent in concerned:
            days = _seeded_days(torrent, now)
            # Date inconnue : on ne suppose jamais que la condition est remplie.
            if days is None or days < conditions.min_seed_days:
                return False
    return True


def eligible_medias(session: Session, rule: AutomationRule) -> list[tuple[Media, list[Torrent]]]:
    status = TRIGGER_STATUSES[rule.trigger]
    now = datetime.now(timezone.utc)
    eligible: list[tuple[Media, list[Torrent]]] = []
    for media in session.exec(select(Media)).all():
        if status not in media.statuses.split(","):
            continue
        torrents = list(session.exec(select(Torrent).where(Torrent.media_id == media.id)).all())
        if matches(rule, media, torrents, now):
            eligible.append((media, torrents))
    return eligible


async def _execute(rule: AutomationRule, session: Session, settings: Settings, media: Media) -> tuple[list[AutomationStep], int]:
    """Exécute l'action de la règle sur un média. Réutilise exactement le code
    des actions manuelles (imports différés : ces modules dépendent du scan)."""
    from app.services.cascade_delete import build_delete_preview, execute_delete
    from app.services.cross_seed import trigger_cross_seed_search
    from app.services.hardlink_repair import execute_repair

    if rule.action == "notify_only":
        return [AutomationStep(label=media.title, success=True)], 0

    try:
        if rule.action == "cleanup":
            freed = build_delete_preview(session, media).total_reclaimable_bytes
            result = await execute_delete(session, media, settings)
            steps = [AutomationStep(label=f"{media.title} — {s.label}", success=s.success, error=s.error) for s in result.steps]
            return steps, freed if all(s.success for s in result.steps) else 0
        if rule.action == "repair_hardlinks":
            result = await execute_repair(session, media, settings)
            steps = [AutomationStep(label=f"{media.title} — {s.label}", success=s.success, error=s.error) for s in result.steps]
            return steps, result.freed_bytes
        torrents = list(session.exec(select(Torrent).where(Torrent.media_id == media.id)).all())
        media_files = list(session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all())
        search = await trigger_cross_seed_search(settings, [t.hash for t in torrents], media_files, scope="episode")
        steps = [AutomationStep(label=media.title, success=True)] * search.triggered + [
            AutomationStep(label=media.title, success=False, error=error) for error in search.errors
        ]
        return steps or [AutomationStep(label=media.title, success=False, error="Aucune recherche déclenchée.")], 0
    except (httpx.HTTPError, OSError, RuntimeError) as exc:
        return [AutomationStep(label=media.title, success=False, error=f"{type(exc).__name__} : {exc}")], 0


async def run_rule(
    session: Session, settings: Settings, channels: list[ChannelTarget], automation: Automation
) -> AutomationRunResult:
    rule = as_rule(automation)
    eligible = eligible_medias(session, rule)
    selected = eligible[: rule.max_actions]

    steps: list[AutomationStep] = []
    freed_total = 0
    for media, _torrents in selected:
        if rule.dry_run:
            steps.append(AutomationStep(label=f"{media.title} (simulation)", success=True))
            continue
        media_steps, freed = await _execute(rule, session, settings, media)
        steps.extend(media_steps)
        freed_total += freed

    automation.last_run_at = datetime.now(timezone.utc)
    automation.last_run_count = len(selected)
    session.add(automation)
    session.commit()

    if selected:
        record_action(
            session,
            "automation",
            MediaRef(id=None, title=rule.name, media_type="movie"),
            steps,
            freed_bytes=freed_total or None,
        )
        notify(
            channels,
            "automation",
            automation_notification(
                notification_language(settings), rule.name, rule.trigger, steps, freed_bytes=freed_total or None
            ),
        )

    return AutomationRunResult(
        automation_id=rule.id,
        name=rule.name,
        matched=len(eligible),
        executed=len(selected),
        dry_run=rule.dry_run,
        freed_bytes=freed_total,
        steps=steps,
    )


async def run_automations(
    session: Session, settings: Settings | None, channels: list[ChannelTarget], trigger: str | None = None
) -> list[AutomationRunResult]:
    """Exécute les règles activées (toutes, ou celles d'un déclencheur donné).
    Sans règle, ne fait rien — et ne touche à rien."""
    if settings is None:
        return []
    query = select(Automation).where(Automation.enabled == True)  # noqa: E712 - SQLModel n'accepte pas `is True`
    if trigger is not None:
        query = query.where(Automation.trigger == trigger)
    return [await run_rule(session, settings, channels, automation) for automation in session.exec(query.order_by(Automation.id)).all()]
