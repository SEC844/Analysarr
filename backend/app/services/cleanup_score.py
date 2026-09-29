"""Score de l'assistant de nettoyage : fonction PURE et déterministe (mêmes
faits, mêmes réglages, même instant = même résultat), sans accès à la base,
au disque ni au réseau. La collecte des faits vit dans services/cleanup.py.

Trois couches :
1. Protections absolues : le média n'est jamais proposé, et la raison est
   dite — favori d'un compte actif, demandé et pas encore vu entièrement par
   son demandeur (demandeur introuvable = protégé, par prudence), ajouté
   récemment, obligation de seed en cours, exclusion manuelle.
2. Score 0-100 : moyenne pondérée de sous-scores normalisés, chacun
   explicable (désintérêt, potentiel restant, ancienneté, statut de la
   série), puis malus si un compte actif est en train de le regarder.
3. Classement : score et espace réellement libérable, combinés par le
   curseur « priorité à l'espace ». L'espace passe par une échelle
   logarithmique, sinon un seul film 4K écraserait tout le classement."""

import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal, NamedTuple

from app.schemas.cleanup import (
    CleanupSettings,
    CleanupWeights,
    ComponentKey,
    PresetName,
    ProtectionRead,
    ScoreComponentRead,
)
from app.services.seed_protection import SeedObligation, obligation_read

PRESETS: dict[PresetName, CleanupSettings] = {
    "prudent": CleanupSettings(
        preset="prudent",
        disinterest_days=548,
        age_days=1095,
        inactive_days=180,
        recent_days=90,
        space_priority=0,
        weights=CleanupWeights(disinterest=40, potential=40, age=10, series=10),
    ),
    "balanced": CleanupSettings(
        preset="balanced",
        disinterest_days=365,
        age_days=730,
        inactive_days=90,
        recent_days=30,
        space_priority=30,
        weights=CleanupWeights(disinterest=35, potential=35, age=15, series=15),
    ),
    "space_first": CleanupSettings(
        preset="space_first",
        disinterest_days=183,
        age_days=365,
        inactive_days=60,
        recent_days=14,
        space_priority=70,
        weights=CleanupWeights(disinterest=30, potential=30, age=20, series=20),
    ),
}

# Raison principale en cas d'égalité de contribution, de la plus parlante à
# la moins parlante.
REASON_ORDER: tuple[ComponentKey, ...] = ("disinterest", "age", "series", "potential")

# Un compte actif en train de regarder : le score est fortement réduit.
IN_PROGRESS_FACTOR = 0.25

# Série terminée : plus rien à attendre ; en cours de diffusion : de nouveaux
# épisodes arriveront ; à venir : pas encore commencée.
SERIES_STATUS_SCORES = {"ended": 100, "deleted": 100, "continuing": 30, "upcoming": 0}


@dataclass(frozen=True, slots=True)
class CandidateFacts:
    """Ce que l'assistant sait d'un média, déjà réduit aux comptes ACTIFS
    (non désactivés, non exclus, activité récente)."""

    media_id: int
    media_type: str
    title: str
    year: int | None
    reclaimable_bytes: int
    date_added: datetime | None = None
    last_played_at: datetime | None = None
    series_status: str | None = None
    # Comptes actifs ayant accès au média, et ceux qui ne l'ont pas terminé.
    active_users: int = 0
    active_unfinished: int = 0
    in_progress_names: tuple[str, ...] = ()
    favorite_names: tuple[str, ...] = ()
    # Demandeurs (Seer/Ombi) qui n'ont pas encore tout vu ; demande dont le
    # demandeur n'a aucun compte connu sur le serveur multimédia.
    unwatched_requesters: tuple[str, ...] = ()
    unknown_requester: bool = False
    seed_obligation: SeedObligation | None = None
    excluded: bool = False
    has_poster: bool = False
    poster_image_tag: str | None = None
    arr_instance_name: str | None = None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _days_since(value: datetime | None, now: datetime) -> int | None:
    return None if value is None else max(0, (now - _as_utc(value)).days)


def _curve(days: int | None, threshold: int) -> int:
    """0 jour = 0, seuil atteint = 100, linéaire entre les deux. Date
    inconnue = 0 : on ne suppose jamais un désintérêt qu'on ne peut pas voir."""
    if days is None:
        return 0
    return round(100 * min(1.0, days / threshold))


def protections(facts: CandidateFacts, settings: CleanupSettings, now: datetime) -> list[ProtectionRead]:
    found: list[ProtectionRead] = []
    if facts.excluded:
        found.append(ProtectionRead(kind="excluded"))
    if facts.favorite_names:
        found.append(ProtectionRead(kind="favorite", names=sorted(facts.favorite_names)))
    if facts.unwatched_requesters:
        found.append(ProtectionRead(kind="request", names=sorted(facts.unwatched_requesters)))
    if facts.unknown_requester:
        found.append(ProtectionRead(kind="request_unknown"))
    added = _days_since(facts.date_added, now)
    if added is not None and added < settings.recent_days:
        found.append(ProtectionRead(kind="recent", days=added))
    if facts.seed_obligation is not None:
        found.append(ProtectionRead(kind="seed", obligation=obligation_read(facts.seed_obligation)))
    return found


@dataclass
class Component:
    """Un sous-score pondéré et de quoi l'expliquer (converti en
    `ScoreComponentRead` seulement pour ce qui est affiché : des milliers
    de modèles pydantic coûteraient plus que tout le calcul)."""

    key: ComponentKey
    value: int
    weight: int = 0
    contribution: float = 0.0
    days: int | None = None
    since: Literal["last_played", "added"] | None = None
    users: int | None = None
    unfinished: int | None = None
    series_status: str | None = None

    def read(self) -> ScoreComponentRead:
        return ScoreComponentRead(**asdict(self))


class _Measures(NamedTuple):
    """Mesures d'un média, calculées une fois : le score (pour tous les
    médias) et le détail (pour ceux qu'on affiche) en dérivent."""

    since_played: int | None
    since_added: int | None
    disinterest_days: int | None
    # (clé, valeur 0-100) de chaque sous-score applicable.
    values: tuple[tuple[ComponentKey, int], ...]


def _measure(facts: CandidateFacts, settings: CleanupSettings, now: datetime) -> _Measures:
    since_played = _days_since(facts.last_played_at, now)
    since_added = _days_since(facts.date_added, now)
    disinterest_days = since_played if since_played is not None else since_added
    # Plus personne d'actif pour le regarder : rien à préserver.
    potential = round(100 * (1 - facts.active_unfinished / facts.active_users)) if facts.active_users else 100
    values: list[tuple[ComponentKey, int]] = [
        ("disinterest", _curve(disinterest_days, settings.disinterest_days)),
        ("potential", potential),
        ("age", _curve(since_added, settings.age_days)),
    ]
    status_score = SERIES_STATUS_SCORES.get(facts.series_status or "")
    if facts.media_type == "series" and status_score is not None:
        values.append(("series", status_score))
    return _Measures(since_played, since_added, disinterest_days, tuple(values))


def _weighted(measures: _Measures, settings: CleanupSettings) -> list[tuple[ComponentKey, int, int, float]]:
    """(clé, valeur, part en %, contribution). Un sous-score qui ne s'applique
    pas (statut de série pour un film) sort du calcul : les autres poids se
    partagent sa part."""
    weights = settings.weights
    by_key = {
        "disinterest": weights.disinterest,
        "potential": weights.potential,
        "age": weights.age,
        "series": weights.series,
    }
    total = sum(by_key[key] for key, _ in measures.values)
    if not total:
        return [(key, value, 0, 0.0) for key, value in measures.values]
    return [
        (key, value, round(100 * by_key[key] / total), round(value * by_key[key] / total, 2))
        for key, value in measures.values
    ]


@dataclass(frozen=True, slots=True)
class Evaluation:
    facts: CandidateFacts
    protections: list[ProtectionRead]
    measures: _Measures
    weighted: list[tuple[ComponentKey, int, int, float]]
    raw_score: int
    score: int

    @property
    def protected(self) -> bool:
        return bool(self.protections)

    @property
    def main_reason(self) -> ComponentKey | None:
        contributing = [(contribution, key) for key, _, _, contribution in self.weighted if contribution > 0]
        # Égalité : l'ordre des critères tranche (le désintérêt parle le plus).
        return max(contributing, key=lambda pair: (pair[0], -REASON_ORDER.index(pair[1])))[1] if contributing else None

    @property
    def components(self) -> list[Component]:
        """Détail explicable, construit seulement pour ce qu'on affiche."""
        m, facts = self.measures, self.facts
        extras: dict[ComponentKey, dict[str, Any]] = {
            "disinterest": {
                "days": m.disinterest_days,
                "since": "last_played"
                if m.since_played is not None
                else ("added" if m.since_added is not None else None),
            },
            "potential": {"users": facts.active_users, "unfinished": facts.active_unfinished},
            "age": {"days": m.since_added},
            "series": {"series_status": facts.series_status},
        }
        return [
            Component(key=key, value=value, weight=weight, contribution=contribution, **extras[key])
            for key, value, weight, contribution in self.weighted
        ]


def evaluate(facts: CandidateFacts, settings: CleanupSettings, now: datetime) -> Evaluation:
    measures = _measure(facts, settings, now)
    weighted = _weighted(measures, settings)
    raw_score = round(sum(contribution for _, _, _, contribution in weighted))
    score = round(raw_score * IN_PROGRESS_FACTOR) if facts.in_progress_names else raw_score
    return Evaluation(facts, protections(facts, settings, now), measures, weighted, raw_score, score)


def space_scale(reclaimable_bytes: int, largest_bytes: int) -> float:
    """Espace sur 0-100, logarithmique, relatif au plus gros candidat."""
    if reclaimable_bytes <= 0 or largest_bytes <= 0:
        return 0.0
    return 100 * math.log1p(reclaimable_bytes) / math.log1p(largest_bytes)


def rank_value(score: int, reclaimable_bytes: int, largest_bytes: int, space_priority: int) -> float:
    priority = space_priority / 100
    return round((1 - priority) * score + priority * space_scale(reclaimable_bytes, largest_bytes), 2)


def ranked(evaluations: list[Evaluation], settings: CleanupSettings) -> list[tuple[Evaluation, float]]:
    """Candidats non protégés, du plus pertinent au moins pertinent. Égalité
    départagée par l'espace, le titre puis l'identifiant : ordre stable."""
    candidates = [e for e in evaluations if not e.protected]
    largest = max((e.facts.reclaimable_bytes for e in candidates), default=0)
    scored = [(e, rank_value(e.score, e.facts.reclaimable_bytes, largest, settings.space_priority)) for e in candidates]
    scored.sort(
        key=lambda pair: (
            -pair[1],
            -pair[0].facts.reclaimable_bytes,
            pair[0].facts.title.lower(),
            pair[0].facts.media_id,
        )
    )
    return scored
