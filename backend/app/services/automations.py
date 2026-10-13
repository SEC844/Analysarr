"""Automatisations : exécuter une action sur les médias qu'un scan vient de
classer (orphelins, doublons, non hardlinkés), ou que l'assistant de
nettoyage propose (score et espace), sous conditions.

Garde-fous, parce qu'une automatisation supprime des fichiers sans que
personne ne regarde :
- rien ne tourne tant que l'utilisateur n'a pas créé de règle ;
- chaque règle ne traite qu'un nombre plafonné de médias par exécution ;
- le mode simulation liste ce qui serait fait sans rien exécuter ;
- les actions réutilisent exactement le code des boutons de l'interface
  (nettoyage cascade, réparation de hardlinks, recherche cross-seed) : un
  torrent protégé ou réparable n'est donc jamais supprimé ;
- chaque exécution est tracée dans l'historique et notifiable.

Déclencheur `cleanup_candidate` (suppression de médias ENTIERS, la plus
lourde des actions), garde-fous supplémentaires :
- score ET espace minimum obligatoires, avec des planchers
  (`MIN_CLEANUP_SCORE`, `MIN_CLEANUP_BYTES`) revérifiés à l'exécution ;
- au plus `MAX_MEDIA_DELETIONS` médias par exécution ;
- protections de l'assistant (favori, demande, ajout récent, seed, exclusion)
  et lecture en cours par un compte actif : jamais proposé, et revérifié
  juste avant chaque suppression ;
- aucune suppression si le visionnage ou les demandes n'ont pas pu être lus
  au dernier scan complet (`source_blocker`) : ces données vides feraient
  passer toute la bibliothèque pour « jamais regardée » et lèveraient les
  protections ;
- suppression par le code du dialogue « Supprimer » (suppression sélective
  « Tout supprimer », tout ou rien, corbeille comprise)."""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlmodel import Session, col, select

from app.models.automation import Automation
from app.models.ids import row_id
from app.models.media import Media, MediaFile, Torrent
from app.models.settings import Settings
from app.schemas.automations import AutomationConditions, AutomationRunResult, AutomationStep
from app.schemas.media import MediaDeleteSelection
from app.services.action_log import MediaRef, record_action
from app.services.arr_link import ArrLinkError, build_link_preview, link_media, pick_automatic
from app.services.cascade_delete import build_delete_preview, execute_delete
from app.services.cleanup import candidate_detail, ranked_candidates, unreliable_sources
from app.services.cross_seed import trigger_cross_seed_search
from app.services.hardlink_repair import execute_repair
from app.services.media_delete import build_delete_footprint, execute_media_delete, reclaimed_bytes
from app.services.notifications import ChannelTarget, automation_notification, notification_language, notify
from app.services.queue_issues import execute_import_retry
from app.services.scan.statuses import is_orphan
from app.services.seed_protection import seed_policy, split_protected

logger = logging.getLogger(__name__)

# Statut (calculé au scan) qui rend un média éligible à chaque déclencheur.
TRIGGER_STATUSES = {
    "orphan_detected": "orphelin_qbit",
    "duplicate_detected": "doublon",
    "non_hardlink_detected": "non_hardlink",
    "import_failed_detected": "import_rate",
    "stalled_download_detected": "telechargement_bloque",
    "untracked_detected": "manquant_arr",
}
MAX_ACTIONS_LIMIT = 50

# Assistant de nettoyage comme déclencheur (voir la docstring du module).
CLEANUP_TRIGGER = "cleanup_candidate"
MIN_CLEANUP_SCORE = 50
MIN_CLEANUP_BYTES = 1024**3
MAX_MEDIA_DELETIONS = 10
# Déclencheurs connus : statuts du scan, plus l'assistant de nettoyage.
TRIGGERS = (*TRIGGER_STATUSES, CLEANUP_TRIGGER)

# Conditions qui ont un sens pour chaque déclencheur. Une condition de seed ou
# de ratio ne veut rien dire pour un import bloqué, et un espace récupérable ne
# veut rien dire là où il n'y a rien à supprimer : elles sont effacées à
# l'enregistrement plutôt que gardées sans effet, et l'interface ne les affiche
# pas (même table dans `frontend/src/types/automations.ts`).
TRIGGER_CONDITIONS: dict[str, tuple[str, ...]] = {
    "orphan_detected": ("min_seed_days", "min_ratio", "min_reclaimable_bytes"),
    "duplicate_detected": ("min_reclaimable_bytes",),
    "non_hardlink_detected": ("min_seed_days", "min_ratio"),
    "import_failed_detected": (),
    "stalled_download_detected": (),
    "untracked_detected": (),
    CLEANUP_TRIGGER: ("min_score", "min_reclaimable_bytes"),
}


def applicable_conditions(trigger: str) -> tuple[str, ...]:
    return TRIGGER_CONDITIONS.get(trigger, ())


@dataclass(frozen=True)
class AutomationRule:
    """Copie d'une règle : utilisable après la fermeture de la session."""

    id: int | None  # None : règle pas encore enregistrée
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
        logger.warning("Conditions illisibles pour l'automatisation %s", automation.id)
        return AutomationConditions()


def max_actions_for(action: str) -> int:
    return MAX_MEDIA_DELETIONS if action == "delete_media" else MAX_ACTIONS_LIMIT


def as_rule(automation: Automation) -> AutomationRule:
    return AutomationRule(
        id=automation.id,
        name=automation.name,
        trigger=automation.trigger,
        action=automation.action,
        conditions=rule_conditions(automation),
        max_actions=min(automation.max_actions, max_actions_for(automation.action)),
        dry_run=automation.dry_run,
    )


def planned_bytes(rule: AutomationRule, media: Media) -> int:
    """Espace que l'action libérerait sur ce média : tout le média pour une
    suppression de l'assistant, l'espace récupérable (doublons, orphelins)
    sinon."""
    return media.full_reclaimable_bytes if rule.trigger == CLEANUP_TRIGGER else media.reclaimable_bytes


# --- Assistant de nettoyage ---------------------------------------------------


def source_blocker(session: Session) -> str | None:
    """Raison de ne RIEN supprimer sur la foi du score : bibliothèque jamais
    analysée, ou visionnage / demandes illisibles au dernier scan complet
    (données vides = toute la bibliothèque « jamais regardée », favoris et
    demandes perdus). Prudent : il faut un nouveau scan complet réussi."""
    failed = unreliable_sources(session)
    if failed is None:
        return "Aucun scan complet terminé : la bibliothèque n'est pas encore connue."
    if "watch" in failed:
        return "Visionnage illisible au dernier scan complet : scores et favoris faussés, aucune suppression."
    if "requests" in failed:
        return "Demandes illisibles au dernier scan complet : protections faussées, aucune suppression."
    return None


def cleanup_targets(session: Session, rule: AutomationRule) -> list[tuple[Media, list[Torrent]]]:
    """Candidats de l'assistant (jamais un média protégé), dans son ordre de
    classement, qui remplissent score ET espace minimum — planchers
    réappliqués ici, même si les conditions enregistrées étaient plus
    basses. Un média qu'un compte actif est en train de regarder est écarté.
    Les torrents ne sont pas chargés (la suppression relit tout le média)."""
    conditions = rule.conditions
    if conditions.min_score is None or conditions.min_reclaimable_bytes is None:
        return []
    min_score = max(conditions.min_score, MIN_CLEANUP_SCORE)
    min_bytes = max(conditions.min_reclaimable_bytes, MIN_CLEANUP_BYTES)
    wanted = [
        evaluation.facts.media_id
        for evaluation, _rank in ranked_candidates(session)
        if evaluation.score >= min_score
        and evaluation.facts.reclaimable_bytes >= min_bytes
        and (not conditions.media_types or evaluation.facts.media_type in conditions.media_types)
        and not evaluation.facts.in_progress_names
    ]
    if not wanted:
        return []
    medias = {media.id: media for media in session.exec(select(Media).where(col(Media.id).in_(wanted))).all()}
    return [(medias[media_id], []) for media_id in wanted if media_id in medias]


async def _delete_whole_media(
    session: Session, settings: Settings, media: Media
) -> tuple[list[AutomationStep], int]:
    """Même suppression que le dialogue de l'assistant : tous les torrents et
    fichiers du média, et son suivi Sonarr/Radarr s'il en a un."""
    detail = candidate_detail(session, row_id(media))
    if detail is None or detail.protections or detail.malus_in_progress:
        return [AutomationStep(label=media.title, success=False, error="Devenu protégé : laissé de côté.")], 0
    selection = MediaDeleteSelection(
        torrent_ids=[row_id(t) for t in session.exec(select(Torrent).where(Torrent.media_id == media.id)).all()],
        media_file_ids=[
            row_id(f) for f in session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
        ],
        remove_from_arr="manquant_arr" not in media.statuses.split(","),
    )
    footprint = await build_delete_footprint(session, media, settings)
    freed = reclaimed_bytes(footprint, selection.torrent_ids, selection.media_file_ids)
    result = await execute_media_delete(session, media, settings, selection)
    steps = [
        AutomationStep(label=f"{detail.title} — {s.label}", success=s.success, error=s.error) for s in result.steps
    ]
    return steps, freed if steps and all(s.success for s in steps) else 0


def _concerned_torrents(trigger: str, torrents: list[Torrent]) -> list[Torrent]:
    """Torrents sur lesquels portent les conditions : les orphelins pour un
    nettoyage, les non hardlinkés pour une réparation. Un torrent ignoré n'est
    jamais concerné."""
    if trigger == "orphan_detected":
        return [t for t in torrents if is_orphan(t) and not t.ignored]
    if trigger == "non_hardlink_detected":
        return [t for t in torrents if t.is_hardlinked is False and t.repairable and not t.ignored]
    return list(torrents)


def _seeded_days(torrent: Torrent, now: datetime) -> float | None:
    reference = torrent.completed_on or torrent.added_on
    if reference is None:
        return None
    return (now - reference.replace(tzinfo=UTC)).total_seconds() / 86400


def matches(rule: AutomationRule, media: Media, torrents: list[Torrent], now: datetime) -> bool:
    conditions = rule.conditions
    if conditions.media_types and media.media_type.value not in conditions.media_types:
        return False
    if conditions.min_reclaimable_bytes and media.reclaimable_bytes < conditions.min_reclaimable_bytes:
        return False

    concerned = _concerned_torrents(rule.trigger, torrents)
    if conditions.min_ratio is not None and (
        not concerned or any((t.ratio or 0) < conditions.min_ratio for t in concerned)
    ):
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
    if rule.trigger == CLEANUP_TRIGGER:
        return cleanup_targets(session, rule)
    status = TRIGGER_STATUSES[rule.trigger]
    now = datetime.now(UTC)
    policy = seed_policy(session.get(Settings, 1))
    eligible: list[tuple[Media, list[Torrent]]] = []
    for media in session.exec(select(Media)).all():
        if status not in media.statuses.split(","):
            continue
        torrents = list(session.exec(select(Torrent).where(Torrent.media_id == media.id)).all())
        if rule.action == "cleanup":
            # Protection du seed : un orphelin qui n'a pas fini son temps de
            # seed n'est ni supprimé (le nettoyage l'écarte) ni pris en compte
            # dans les conditions. Plus aucun orphelin supprimable : la règle
            # n'a rien à faire sur ce média.
            torrents, _protected = split_protected(torrents, policy, now)
            if rule.trigger == "orphan_detected" and not _concerned_torrents(rule.trigger, torrents):
                continue
        if matches(rule, media, torrents, now):
            eligible.append((media, torrents))
    return eligible


async def _execute(
    rule: AutomationRule, session: Session, settings: Settings, media: Media
) -> tuple[list[AutomationStep], int]:
    """Exécute l'action de la règle sur un média. Réutilise exactement le code
    des actions manuelles (imports différés : ces modules dépendent du scan)."""

    if rule.action == "notify_only":
        return [AutomationStep(label=media.title, success=True)], 0

    try:
        if rule.action == "delete_media":
            return await _delete_whole_media(session, settings, media)
        if rule.action == "cleanup":
            freed = build_delete_preview(session, media).total_reclaimable_bytes
            result = await execute_delete(session, media, settings)
            steps = [
                AutomationStep(label=f"{media.title} — {s.label}", success=s.success, error=s.error)
                for s in result.steps
            ]
            return steps, freed if all(s.success for s in result.steps) else 0
        if rule.action == "retry_import":
            # Relance l'import bloqué : aucune suppression, donc aucun risque
            # de perdre un fichier — l'action la plus sûre du lot.
            import_steps, _imported = await execute_import_retry(session, media, settings)
            steps = [
                AutomationStep(label=f"{media.title} — {s.label}", success=s.success, error=s.error)
                for s in import_steps
            ]
            return steps or [AutomationStep(label=media.title, success=False, error="Aucun import bloqué.")], 0
        if rule.action == "link_to_arr":
            # Rattachement automatique : uniquement sur un candidat CERTAIN
            # (identifiant résolu par Sonarr/Radarr, titre et année
            # concordants) et un dossier racine déduit des fichiers en place.
            # Tout le reste attend une confirmation humaine.
            preview = await build_link_preview(session, settings, media)
            candidate = pick_automatic(preview)
            profile = preview.suggested_profile  # garanti par pick_automatic
            if candidate is None or profile is None:
                return [AutomationStep(label=media.title, success=False, error="Aucune correspondance certaine.")], 0
            title = await link_media(
                session,
                settings,
                media,
                candidate_key=candidate.key,
                quality_profile_id=profile,
            )
            return [AutomationStep(label=f"{media.title} — {title}", success=True)], 0
        if rule.action == "repair_hardlinks":
            repair = await execute_repair(session, media, settings)
            steps = [
                AutomationStep(label=f"{media.title} — {s.label}", success=s.success, error=s.error)
                for s in repair.steps
            ]
            return steps, repair.freed_bytes
        torrents = list(session.exec(select(Torrent).where(Torrent.media_id == media.id)).all())
        media_files = list(session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all())
        search = await trigger_cross_seed_search(settings, [t.hash for t in torrents], media_files, scope="episode")
        steps = [AutomationStep(label=media.title, success=True)] * search.triggered + [
            AutomationStep(label=media.title, success=False, error=error) for error in search.errors
        ]
        return steps or [AutomationStep(label=media.title, success=False, error="Aucune recherche déclenchée.")], 0
    except (httpx.HTTPError, OSError, RuntimeError, ArrLinkError) as exc:
        return [AutomationStep(label=media.title, success=False, error=f"{type(exc).__name__} : {exc}")], 0


async def run_rule(
    session: Session, settings: Settings, channels: list[ChannelTarget], automation: Automation
) -> AutomationRunResult:
    rule = as_rule(automation)
    blocker = source_blocker(session) if rule.trigger == CLEANUP_TRIGGER else None
    eligible = [] if blocker else eligible_medias(session, rule)
    selected = eligible[: rule.max_actions]

    steps: list[AutomationStep] = (
        [AutomationStep(label=rule.name, success=False, error=blocker)] if blocker else []
    )
    freed_total = 0
    for media, _torrents in selected:
        if rule.dry_run:
            steps.append(AutomationStep(label=f"{media.title} (simulation)", success=True))
            continue
        media_steps, freed = await _execute(rule, session, settings, media)
        steps.extend(media_steps)
        freed_total += freed

    automation.last_run_at = datetime.now(UTC)
    automation.last_run_count = len(selected)
    session.add(automation)
    session.commit()

    if selected or blocker:
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
        automation_id=row_id(automation),
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
    return [
        await run_rule(session, settings, channels, automation)
        for automation in session.exec(query.order_by(col(Automation.id))).all()
    ]
