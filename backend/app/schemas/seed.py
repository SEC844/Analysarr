import re
from datetime import datetime

from pydantic import BaseModel, Field, TypeAdapter, field_validator

MIN_SEED_DAYS = 0
MAX_SEED_DAYS = 365
MAX_SEED_RATIO = 100.0
MAX_TRACKER_RULES = 50

# Nom d'hôte, tel que l'extrait services/trackers.py (jamais d'URL, de port ni
# de passkey) : étiquettes DNS séparées par des points.
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")


class TrackerSeedRule(BaseModel):
    """Surcharge pour un tracker : durée minimale ET, si renseigné, ratio
    minimum — les deux doivent être atteints avant qu'une suppression soit
    permise."""

    domain: str = Field(max_length=253)
    min_days: int = Field(ge=MIN_SEED_DAYS, le=MAX_SEED_DAYS)
    min_ratio: float | None = Field(default=None, ge=0, le=MAX_SEED_RATIO)

    @field_validator("domain")
    @classmethod
    def _valid_domain(cls, value: str) -> str:
        domain = value.strip().lower().rstrip(".")
        if not _DOMAIN.match(domain):
            raise ValueError("Domaine de tracker invalide (exemple : tracker.exemple.org).")
        return domain


TRACKER_RULES = TypeAdapter(list[TrackerSeedRule])


class SeedProtectionWrite(BaseModel):
    enabled: bool
    private_min_days: int = Field(ge=MIN_SEED_DAYS, le=MAX_SEED_DAYS)
    public_enabled: bool
    public_min_days: int = Field(ge=MIN_SEED_DAYS, le=MAX_SEED_DAYS)
    tracker_rules: list[TrackerSeedRule] = Field(default_factory=list, max_length=MAX_TRACKER_RULES)

    @field_validator("tracker_rules")
    @classmethod
    def _unique_domains(cls, rules: list[TrackerSeedRule]) -> list[TrackerSeedRule]:
        domains = [rule.domain for rule in rules]
        if len(set(domains)) != len(domains):
            raise ValueError("Un même tracker ne peut avoir qu'une seule surcharge.")
        return rules


class SeedProtectionRead(SeedProtectionWrite):
    # Bandeau proposé à une installation existante (protection désactivée et
    # jamais refusée).
    prompt: bool
    # Domaines des trackers vus au dernier scan, proposés à la saisie.
    known_trackers: list[str]
    min_days: int = MIN_SEED_DAYS
    max_days: int = MAX_SEED_DAYS
    max_ratio: float = MAX_SEED_RATIO
    max_rules: int = MAX_TRACKER_RULES


class SeedObligationRead(BaseModel):
    """Obligation de seed en cours : `min_seed` (jusqu'à `until`), `min_ratio`
    (durée atteinte, ratio pas encore), `unknown_date` (aucune date de
    référence : protégé sans échéance)."""

    reason: str
    until: datetime | None
    tracker: str | None
    min_days: int
    min_ratio: float | None
