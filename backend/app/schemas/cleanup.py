from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.seed import SeedObligationRead

PresetName = Literal["prudent", "balanced", "space_first"]


class CleanupWeights(BaseModel):
    """Poids relatifs des sous-scores (au moins un non nul)."""

    disinterest: int = Field(ge=0, le=100)
    potential: int = Field(ge=0, le=100)
    age: int = Field(ge=0, le=100)
    series: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _not_all_zero(self) -> "CleanupWeights":
        if not (self.disinterest or self.potential or self.age or self.series):
            raise ValueError("Au moins un poids doit être supérieur à zéro.")
        return self


class CleanupSettings(BaseModel):
    """Réglages de l'assistant de nettoyage, bornés côté serveur. `preset` :
    préréglage de départ (None = réglages personnalisés)."""

    preset: PresetName | None = "balanced"
    # Jours sans lecture (ou depuis l'ajout, jamais vu) pour un désintérêt de 100.
    disinterest_days: int = Field(default=365, ge=30, le=1825)
    # Jours depuis l'ajout pour une ancienneté de 100.
    age_days: int = Field(default=730, ge=30, le=3650)
    # Un compte sans activité depuis ce nombre de jours ne compte plus.
    inactive_days: int = Field(default=90, ge=7, le=730)
    # Ajouté depuis moins de ce nombre de jours : protégé.
    recent_days: int = Field(default=30, ge=0, le=365)
    # 0 = classement par score seul, 100 = espace libérable prioritaire.
    space_priority: int = Field(default=30, ge=0, le=100)
    weights: CleanupWeights = Field(
        default_factory=lambda: CleanupWeights(disinterest=35, potential=35, age=15, series=15)
    )


class CleanupSettingsRead(BaseModel):
    settings: CleanupSettings
    presets: dict[str, CleanupSettings]


ComponentKey = Literal["disinterest", "potential", "age", "series"]


class ScoreComponentRead(BaseModel):
    """Un sous-score et de quoi l'expliquer : `days` (désintérêt, ancienneté),
    `unfinished`/`users` (potentiel restant), `series_status` (séries).
    `weight` : part effective en % (poids redistribués quand un sous-score ne
    s'applique pas, ex. statut de série pour un film)."""

    key: ComponentKey
    value: int
    weight: int
    contribution: float
    days: int | None = None
    since: Literal["last_played", "added"] | None = None
    users: int | None = None
    unfinished: int | None = None
    series_status: str | None = None


ProtectionKind = Literal["favorite", "request", "request_unknown", "recent", "seed", "excluded"]


class ProtectionRead(BaseModel):
    """Raison pour laquelle un média n'est jamais proposé. `names` : comptes
    concernés (favori, demandeur) ; `days` : ajouté depuis ce nombre de jours ;
    `obligation` : obligation de seed en cours."""

    kind: ProtectionKind
    names: list[str] = Field(default_factory=list)
    days: int | None = None
    obligation: SeedObligationRead | None = None


class CleanupCandidateRead(BaseModel):
    media_id: int
    media_type: str
    title: str
    year: int | None
    has_poster: bool
    poster_image_tag: str | None
    arr_instance_name: str | None = None
    score: int
    rank: float
    # Espace réellement libéré en supprimant tout le média.
    reclaimable_bytes: int
    # Sous-score qui pèse le plus : raison principale affichée.
    main_reason: ComponentKey | None
    malus_in_progress: bool
    protections: list[ProtectionRead]
    last_played_at: datetime | None
    date_added: datetime | None
    # De quoi formuler la raison principale sans charger le détail.
    series_status: str | None = None
    active_users: int = 0


class CleanupCandidateDetail(CleanupCandidateRead):
    components: list[ScoreComponentRead]
    # Score avant le malus « en cours de visionnage ».
    raw_score: int
    in_progress_users: list[str]


class CleanupCandidatesPage(BaseModel):
    items: list[CleanupCandidateRead]
    total: int
    # Médias proposés (non protégés) correspondant aux filtres, et l'espace
    # que leur suppression libérerait (somme : exacte tant qu'aucun fichier
    # n'est hardlinké entre deux médias).
    candidate_count: int
    protected_count: int
    total_reclaimable_bytes: int


# --- Mode objectif (services/cleanup_plan.py) ---------------------------------

# Clé d'un disque de la prévision : ses racines jointes par « + ».
_DISK_KEY = r"^(library|downloads)(\+(library|downloads))?$"


class CleanupPlanRequest(BaseModel):
    """`free` : libérer `target_bytes` octets. `until` : que le disque `disk`
    tienne jusqu'au `until` (espace à libérer tiré de la prévision)."""

    goal: Literal["free", "until"]
    # 1 Mo à 1 Po.
    target_bytes: int | None = Field(default=None, ge=1_000_000, le=10**15)
    until: date | None = None
    disk: str | None = Field(default=None, max_length=40, pattern=_DISK_KEY)

    @model_validator(mode="after")
    def _goal_fields(self) -> "CleanupPlanRequest":
        if self.goal == "free" and self.target_bytes is None:
            raise ValueError("Indiquez l'espace à libérer.")
        if self.goal == "until" and (self.until is None or self.disk is None):
            raise ValueError("Indiquez la date et le disque.")
        return self


class PlanLossMedia(BaseModel):
    media_id: int
    title: str
    media_type: str
    in_progress: bool
    favorite: bool
    # Films : pourcentage de lecture (0-100). Séries : nombre d'épisodes vus.
    progress: float


class PlanLossRead(BaseModel):
    """Un compte et ce qu'il perdrait : médias commencés ou mis en favori."""

    user_id: str
    name: str
    image_tag: str | None
    media: list[PlanLossMedia]


class CleanupPlanRead(BaseModel):
    """Simulation, sans aucun effet. `target_bytes` : espace à libérer
    (0 en mode `until` quand le disque tient déjà). `shortfall_bytes` : ce
    qui manque, faute de candidats (ou plafond `max_items` atteint)."""

    goal: Literal["free", "until"]
    target_bytes: int
    freed_bytes: int
    shortfall_bytes: int
    limited: bool
    max_items: int
    items: list[CleanupCandidateRead]
    losses: list[PlanLossRead]
    # Mode `until` : disque visé, espace libre aujourd'hui, jours jusqu'à la
    # date et croissance prudente retenue (octets par jour).
    disk: str | None = None
    free_bytes: int | None = None
    days: int | None = None
    growth_per_day: float | None = None
