"""Garde-fou des automatisations : mise en pause après un basculement massif
des statuts.

Protection contre la perte de toute une bibliothèque : un montage absent, un
partage injoignable ou un client torrent réinitialisé suffit à faire passer
des centaines de médias pour des orphelins ou des doublons — et une
automatisation de nettoyage les supprimerait tous.

Un scan ne sait pas POURQUOI un média change d'état. Un partage devenu
injoignable, un client torrent remis à zéro ou une bibliothèque déplacée font
basculer d'un coup des dizaines de médias en orphelin, doublon ou non
hardlinké — exactement les déclencheurs des automatisations. Plutôt que de
laisser une règle « nettoyer les orphelins » agir sur cette fausse tendance,
les automatisations se coupent et attendent une reprise manuelle.

Le changement est mesuré en part de la bibliothèque (et non en variation
relative du compteur) : 3 orphelins qui passent à 6, c'est le quotidien ; 200
médias sur 400 qui basculent, c'est un incident. Seules les hausses sont
surveillées : une chute massive n'offre aucune cible de plus aux règles. Une action de l'utilisateur
depuis le scan précédent (suppression, nettoyage, réparation, automatisation)
explique le changement : aucune pause dans ce cas."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeGuard

from sqlmodel import Session, col, select

from app.models.activity import ActionLog
from app.models.automation import Automation
from app.models.media import ScanRun, ScanStatus
from app.models.settings import Settings

GUARDED_STATUSES = ("doublon", "orphelin_qbit", "non_hardlink")
# Plancher : en dessous, la moindre variation normale couperait tout.
MIN_THRESHOLD_PERCENT = 5
DEFAULT_THRESHOLD_PERCENT = 20


@dataclass(frozen=True)
class MassChange:
    status: str
    previous: int
    current: int
    total: int
    percent: int

    def as_json(self) -> str:
        return json.dumps(
            {
                "status": self.status,
                "previous": self.previous,
                "current": self.current,
                "total": self.total,
                "percent": self.percent,
            }
        )


def threshold_percent(settings: Settings | None) -> int:
    value = settings.automation_guard_percent if settings else DEFAULT_THRESHOLD_PERCENT
    return max(MIN_THRESHOLD_PERCENT, min(100, value))


def watched_statuses(session: Session) -> set[str]:
    """Statuts réellement surveillés : ceux qui déclenchent au moins une
    automatisation activée. Sans automatisation sur les orphelins, doublons ou
    torrents non hardlinkés, le garde-fou n'a rien à protéger — il ne
    s'applique pas et ne s'affiche pas."""
    from app.services.automations import TRIGGER_STATUSES

    active = session.exec(select(Automation).where(Automation.enabled == True)).all()  # noqa: E712
    return {TRIGGER_STATUSES.get(rule.trigger, "") for rule in active} & set(GUARDED_STATUSES)


def _counts(run: ScanRun) -> dict[str, int]:
    return {
        "doublon": run.duplicate_count,
        "orphelin_qbit": run.orphan_count,
        "non_hardlink": run.non_hardlink_count,
    }


def previous_full_run(session: Session, run: ScanRun) -> ScanRun | None:
    """Dernier scan COMPLET terminé avant celui-ci : comparer un scan complet à
    une analyse par service (qui ne couvre qu'une partie des médias) ferait
    voir un effondrement là où il n'y a qu'un périmètre différent."""
    query = (
        select(ScanRun)
        .where(ScanRun.id != run.id, ScanRun.scope == "full", ScanRun.status == ScanStatus.completed)
        .order_by(col(ScanRun.id).desc())
        .limit(1)
    )
    return session.exec(query).first()


def _user_acted_since(session: Session, since: datetime | None) -> bool:
    if since is None:
        return False
    return session.exec(select(ActionLog.id).where(ActionLog.created_at > since).limit(1)).first() is not None


def detect_mass_change(session: Session, run: ScanRun, settings: Settings | None) -> MassChange | None:
    """Plus gros basculement de statut depuis le scan complet précédent, s'il
    dépasse le seuil et qu'aucune action de l'utilisateur ne l'explique."""
    watched = watched_statuses(session)
    if not watched:
        return None
    previous = previous_full_run(session, run)
    if previous is None or run.media_count <= 0:
        return None
    if _user_acted_since(session, previous.finished_at):
        return None

    limit = threshold_percent(settings)
    worst: MassChange | None = None
    before, after = _counts(previous), _counts(run)
    for status in sorted(watched):
        # Seules les HAUSSES comptent : une chute massive (montage retrouvé,
        # nettoyage manuel) ne donne aucune cible supplémentaire aux règles, et
        # remettre en pause au retour à la normale serait pénible pour rien.
        gained = after[status] - before[status]
        if gained <= 0:
            continue
        percent = round(gained * 100 / run.media_count)
        if percent >= limit and (worst is None or percent > worst.percent):
            worst = MassChange(
                status=status,
                previous=before[status],
                current=after[status],
                total=run.media_count,
                percent=percent,
            )
    return worst


def pause_automations(session: Session, settings: Settings, change: MassChange) -> None:
    settings.automations_paused_at = datetime.now(UTC)
    settings.automations_paused_reason = change.as_json()
    session.add(settings)
    session.commit()


def resume_automations(session: Session, settings: Settings) -> None:
    settings.automations_paused_at = None
    settings.automations_paused_reason = None
    session.add(settings)
    session.commit()


def is_paused(settings: Settings | None) -> TypeGuard[Settings]:
    return bool(settings and settings.automations_paused_at is not None)


def paused_reason(settings: Settings | None) -> dict | None:
    if not is_paused(settings) or not settings.automations_paused_reason:
        return None
    try:
        reason = json.loads(settings.automations_paused_reason)
    except ValueError:
        return None
    return reason if isinstance(reason, dict) else None
