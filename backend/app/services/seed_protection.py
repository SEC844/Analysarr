"""Protection du seed : un torrent qui n'a pas encore rempli son obligation de
partage n'est jamais supprimé par un nettoyage ni par une automatisation.

Les trackers privés sanctionnent le « hit and run » (torrent retiré avant la
durée de seed exigée). Analysarr ne connaît pas les règles de chaque tracker :
l'administrateur fixe une durée pour les torrents privés, des surcharges par
tracker (durée ET ratio minimum facultatif, les deux exigés), et, s'il le
souhaite, une durée pour les torrents publics.

Règles de sûreté :
- un torrent dont le client ne dit pas s'il est privé est traité comme privé ;
- un torrent sans date de fin de téléchargement (ni d'ajout) est protégé sans
  échéance : on ne suppose jamais qu'une obligation est remplie ;
- un ratio illisible n'est jamais considéré comme atteint.

Fonction pure (`seed_obligation`) : aucun accès au réseau ni au disque, pour
être appelée sur chaque torrent de la bibliothèque sans coût notable."""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Literal, Protocol

from pydantic import ValidationError

from app.models.media import Torrent
from app.models.settings import Settings
from app.schemas.seed import TRACKER_RULES, SeedObligationRead, TrackerSeedRule

logger = logging.getLogger(__name__)

ObligationReason = Literal["min_seed", "min_ratio", "unknown_date"]


class SeedFacts(Protocol):
    """Ce dont la règle a besoin : un `Torrent`, ou une ligne lue colonne par
    colonne (l'assistant de nettoyage évalue des milliers de torrents : les
    charger comme objets ORM coûterait plus que tout le reste du calcul)."""

    @property
    def is_private(self) -> bool | None: ...
    @property
    def completed_on(self) -> datetime | None: ...
    @property
    def added_on(self) -> datetime | None: ...
    @property
    def ratio(self) -> float | None: ...
    @property
    def trackers_json(self) -> str: ...


@dataclass(frozen=True)
class SeedPolicy:
    enabled: bool = False
    private_min_days: int = 14
    public_enabled: bool = False
    public_min_days: int = 3
    tracker_rules: tuple[TrackerSeedRule, ...] = ()

    def rule_for(self, domain: str) -> TrackerSeedRule | None:
        """Surcharge d'un tracker : domaine identique, ou sous-domaine
        (`tracker.exemple.org` pour une règle `exemple.org`)."""
        for rule in self.tracker_rules:
            if domain == rule.domain or domain.endswith("." + rule.domain):
                return rule
        return None


@dataclass(frozen=True)
class SeedObligation:
    """Obligation non remplie. `until` : fin de la durée minimale (None si la
    date de référence est inconnue) ; `tracker` : domaine en cause (None =
    règle générale, torrent sans tracker lisible)."""

    reason: ObligationReason
    until: datetime | None
    tracker: str | None
    min_days: int
    min_ratio: float | None = None


def tracker_rules(settings: Settings | None) -> list[TrackerSeedRule]:
    raw = settings.seed_tracker_rules if settings else "[]"
    try:
        return TRACKER_RULES.validate_json(raw or "[]")
    except ValidationError:
        logger.warning("Surcharges de seed par tracker illisibles : ignorées")
        return []


def seed_policy(settings: Settings | None) -> SeedPolicy:
    if settings is None:
        return SeedPolicy()
    return SeedPolicy(
        enabled=settings.seed_protection_enabled,
        private_min_days=settings.seed_private_min_days,
        public_enabled=settings.seed_public_enabled,
        public_min_days=settings.seed_public_min_days,
        tracker_rules=tuple(tracker_rules(settings)),
    )


def prompt_pending(settings: Settings | None) -> bool:
    """Bandeau proposé aux installations existantes : protection désactivée
    et jamais refusée."""
    return (
        settings is not None and not settings.seed_protection_enabled and not settings.seed_protection_prompt_dismissed
    )


def _domains(torrent: SeedFacts) -> tuple[str, ...]:
    return _parse_domains(torrent.trackers_json or "[]")


@lru_cache(maxsize=4096)
def _parse_domains(trackers_json: str) -> tuple[str, ...]:
    """Domaines d'une liste de trackers (texte JSON). Mis en cache : la plupart
    des torrents d'une bibliothèque partagent la même liste de trackers."""
    try:
        entries = json.loads(trackers_json)
    except ValueError:
        return ()
    if not isinstance(entries, list):
        return ()
    return tuple(
        d["domain"] for d in entries if isinstance(d, dict) and isinstance(d.get("domain"), str) and d["domain"]
    )


def _requirements(torrent: SeedFacts, policy: SeedPolicy) -> list[tuple[str | None, int, float | None]]:
    """(tracker, jours, ratio) à respecter. Une surcharge vaut pour son
    tracker, que le torrent soit privé ou public ; sinon la règle générale."""
    private = torrent.is_private is not False  # inconnu = privé
    general = policy.private_min_days if private else (policy.public_min_days if policy.public_enabled else None)
    requirements: list[tuple[str | None, int, float | None]] = []
    domains = _domains(torrent)
    for domain in domains:
        rule = policy.rule_for(domain)
        if rule is not None:
            requirements.append((domain, rule.min_days, rule.min_ratio))
        elif general is not None:
            requirements.append((domain, general, None))
    if not domains and general is not None:
        requirements.append((None, general, None))
    return requirements


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _pending(
    torrent: SeedFacts, tracker: str | None, days: int, ratio: float | None, now: datetime
) -> SeedObligation | None:
    reference = torrent.completed_on or torrent.added_on
    if reference is None:
        return SeedObligation("unknown_date", None, tracker, days, ratio)
    until = _as_utc(reference) + timedelta(days=days)
    if now < until:
        return SeedObligation("min_seed", until, tracker, days, ratio)
    if ratio is not None and (torrent.ratio is None or torrent.ratio < ratio):
        return SeedObligation("min_ratio", until, tracker, days, ratio)
    return None


def _strength(obligation: SeedObligation) -> tuple[int, datetime]:
    """Obligation la plus contraignante : date inconnue, puis ratio (sans
    échéance connue), puis la durée qui se termine le plus tard."""
    rank = {"unknown_date": 2, "min_ratio": 1, "min_seed": 0}[obligation.reason]
    return rank, obligation.until or datetime.max.replace(tzinfo=UTC)


def strongest(obligations: list[SeedObligation]) -> SeedObligation | None:
    """Obligation la plus contraignante d'une liste (celle qu'on affiche)."""
    return max(obligations, key=_strength) if obligations else None


def seed_obligation(torrent: SeedFacts, policy: SeedPolicy, now: datetime | None = None) -> SeedObligation | None:
    """Obligation de seed encore en cours pour ce torrent, ou None s'il peut
    être supprimé (protection désactivée, ou toutes les exigences remplies)."""
    if not policy.enabled:
        return None
    now = now or datetime.now(UTC)
    pending = [
        obligation
        for tracker, days, ratio in _requirements(torrent, policy)
        if (obligation := _pending(torrent, tracker, days, ratio, now)) is not None
    ]
    return strongest(pending)


def split_protected(
    torrents: list[Torrent], policy: SeedPolicy, now: datetime | None = None
) -> tuple[list[Torrent], list[tuple[Torrent, SeedObligation]]]:
    """(torrents supprimables, torrents protégés avec leur obligation)."""
    now = now or datetime.now(UTC)
    free: list[Torrent] = []
    protected: list[tuple[Torrent, SeedObligation]] = []
    for torrent in torrents:
        obligation = seed_obligation(torrent, policy, now)
        if obligation is None:
            free.append(torrent)
        else:
            protected.append((torrent, obligation))
    return free, protected


def obligation_read(obligation: SeedObligation | None) -> SeedObligationRead | None:
    if obligation is None:
        return None
    return SeedObligationRead(
        reason=obligation.reason,
        until=obligation.until,
        tracker=obligation.tracker,
        min_days=obligation.min_days,
        min_ratio=obligation.min_ratio,
    )
