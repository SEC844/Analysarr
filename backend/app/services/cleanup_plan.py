"""Mode objectif de l'assistant de nettoyage : « libérer X Go » ou « tenir
jusqu'au JJ/MM ». SIMULATION seulement — rien n'est supprimé ici : la
sélection proposée remplit la sélection de l'assistant, et la suppression
passe par son dialogue habituel (aperçu, confirmation, revérification des
protections, suppression sélective « Tout supprimer » média par média).

Sélection gloutonne dans l'ordre de l'assistant (rang = score et espace
combinés selon « priorité à l'espace ») : seuls les candidats NON protégés
sont considérés, la couche des protections reste absolue. Puis élagage :
du moins pertinent au plus pertinent, tout média devenu inutile pour
atteindre l'objectif (parce qu'un média plus gros a suffi) est retiré —
supprimer moins, à objectif atteint.

« Tenir jusqu'au » : espace à libérer = croissance PRUDENTE (pente haute de
la prévision) jusqu'à la date, moins l'espace libre, plus 5 % de marge.

« Qui perd quoi » : pour chaque compte non désactivé et non exclu des
statistiques, les médias sélectionnés qu'il a commencés ou mis en favori.
Les favoris des comptes actifs protègent déjà le média ; restent ici ceux
des comptes inactifs, et les lectures en cours (malus du score, jamais une
protection)."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlmodel import Session, col, select

from app.models.media import EmbyUser, MediaWatch
from app.models.settings import Settings
from app.schemas.cleanup import CleanupPlanRead, CleanupPlanRequest, PlanLossMedia, PlanLossRead
from app.services.cleanup import candidate_read, ranked_candidates
from app.services.cleanup_score import Evaluation
from app.services.forecast import MIN_HISTORY_DAYS, bytes_to_free, load_forecast
from app.services.watch_stats import excluded_user_ids

# Garde-fou : une simulation ne propose jamais plus de médias d'un coup.
MAX_PLAN_ITEMS = 200
# « Tenir jusqu'au » : au plus un an, au-delà la prévision n'a plus de sens.
MAX_PLAN_DAYS = 365
SAFETY_MARGIN = 0.05


class PlanError(ValueError):
    """Objectif impossible à calculer ; le message est affiché tel quel."""


@dataclass(frozen=True)
class Selection:
    chosen: list[tuple[Evaluation, float]]
    freed: int
    limited: bool


def select_for_goal(candidates: Sequence[tuple[Evaluation, float]], target: int, max_items: int) -> Selection:
    """Fonction pure. `candidates` : non protégés, du plus pertinent au
    moins pertinent. Un média qui ne libère rien n'est jamais retenu."""
    chosen: list[tuple[Evaluation, float]] = []
    freed = 0
    limited = False
    for candidate in candidates:
        if freed >= target:
            break
        size = candidate[0].facts.reclaimable_bytes
        if size <= 0:
            continue
        if len(chosen) == max_items:
            limited = True
            break
        chosen.append(candidate)
        freed += size
    if freed >= target:
        for candidate in reversed(list(chosen)):
            size = candidate[0].facts.reclaimable_bytes
            if freed - size >= target:
                chosen.remove(candidate)
                freed -= size
    return Selection(chosen, freed, limited)


@dataclass(frozen=True)
class _Target:
    bytes: int
    disk: str | None = None
    free: int | None = None
    days: int | None = None
    growth_per_day: float | None = None


def _until_target(session: Session, request: CleanupPlanRequest, now: datetime) -> _Target:
    if request.until is None or request.disk is None:  # refusé plus tôt par le schéma
        raise PlanError("Indiquez la date et le disque.")
    if not now.date() < request.until <= now.date() + timedelta(days=MAX_PLAN_DAYS):
        raise PlanError(f"Choisissez une date dans les {MAX_PLAN_DAYS} prochains jours.")
    forecast = load_forecast(session)
    disk = forecast.disk(request.disk)
    if disk is None or forecast.latest_day is None:
        raise PlanError("Disque inconnu à la dernière mesure.")
    if disk.trend is None or disk.free is None:
        raise PlanError(
            f"Pas assez d'historique pour prévoir ce disque ({disk.history_days} jours mesurés sur "
            f"{MIN_HISTORY_DAYS} nécessaires)."
        )
    days = (request.until - forecast.latest_day).days
    return _Target(
        bytes=bytes_to_free(disk.free, disk.trend, days, SAFETY_MARGIN),
        disk=disk.key,
        free=disk.free,
        days=days,
        growth_per_day=disk.trend.high,
    )


def _losses(session: Session, chosen: Sequence[tuple[Evaluation, float]]) -> list[PlanLossRead]:
    if not chosen:
        return []
    excluded = excluded_user_ids(session.get(Settings, 1))
    users = {
        user.id: user
        for user in session.exec(select(EmbyUser)).all()
        if not user.is_disabled and user.id not in excluded
    }
    facts = {e.facts.media_id: e.facts for e, _ in chosen}
    order = {media_id: position for position, media_id in enumerate(facts)}
    rows = session.exec(
        select(MediaWatch).where(
            col(MediaWatch.media_id).in_(list(facts)),
            or_(col(MediaWatch.in_progress), col(MediaWatch.favorite)),
        )
    ).all()
    lost: dict[str, list[PlanLossMedia]] = defaultdict(list)
    for watch in sorted(rows, key=lambda w: order[w.media_id]):
        if watch.emby_user_id not in users:
            continue
        media = facts[watch.media_id]
        lost[watch.emby_user_id].append(
            PlanLossMedia(
                media_id=media.media_id,
                title=media.title,
                media_type=media.media_type,
                in_progress=watch.in_progress,
                favorite=watch.favorite,
                progress=watch.progress,
            )
        )
    return sorted(
        (
            PlanLossRead(user_id=user_id, name=users[user_id].name, image_tag=users[user_id].image_tag, media=media)
            for user_id, media in lost.items()
        ),
        key=lambda loss: loss.name.casefold(),
    )


def build_plan(session: Session, request: CleanupPlanRequest, now: datetime | None = None) -> CleanupPlanRead:
    now = now or datetime.now(UTC)
    if request.goal == "until":
        target = _until_target(session, request, now)
    elif request.target_bytes is not None:
        target = _Target(bytes=request.target_bytes)
    else:  # refusé plus tôt par le schéma
        raise PlanError("Indiquez l'espace à libérer.")
    selection = (
        select_for_goal(ranked_candidates(session, now), target.bytes, MAX_PLAN_ITEMS)
        if target.bytes > 0
        else Selection([], 0, False)
    )
    return CleanupPlanRead(
        goal=request.goal,
        target_bytes=target.bytes,
        freed_bytes=selection.freed,
        shortfall_bytes=max(0, target.bytes - selection.freed),
        limited=selection.limited,
        max_items=MAX_PLAN_ITEMS,
        items=[candidate_read(evaluation, rank) for evaluation, rank in selection.chosen],
        losses=_losses(session, selection.chosen),
        disk=target.disk,
        free_bytes=target.free,
        days=target.days,
        growth_per_day=target.growth_per_day,
    )
