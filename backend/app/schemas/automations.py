from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AutomationTrigger = Literal[
    "orphan_detected",
    "duplicate_detected",
    "non_hardlink_detected",
    "import_failed_detected",
    "stalled_download_detected",
    "untracked_detected",
]
AutomationAction = Literal[
    "cleanup", "repair_hardlinks", "cross_seed_search", "retry_import", "link_to_arr", "notify_only"
]


class AutomationConditions(BaseModel):
    """Toutes optionnelles : une condition absente ne filtre rien. Elles
    s'appliquent au média ET aux torrents concernés par l'action."""

    # Vide = films et séries.
    media_types: list[Literal["movie", "series"]] = []
    # Ancienneté minimale du seed, en jours, pour CHAQUE torrent concerné.
    min_seed_days: int | None = Field(default=None, ge=0, le=3650)
    # Ratio minimal de CHAQUE torrent concerné.
    min_ratio: float | None = Field(default=None, ge=0, le=1000)
    # Espace récupérable minimal du média.
    min_reclaimable_bytes: int | None = Field(default=None, ge=0)


class AutomationRead(BaseModel):
    id: int
    name: str
    enabled: bool
    trigger: AutomationTrigger
    action: AutomationAction
    conditions: AutomationConditions
    max_actions: int
    dry_run: bool
    last_run_at: datetime | None
    last_run_count: int


class AutomationWrite(BaseModel):
    name: str = Field(max_length=40)
    enabled: bool = True
    trigger: AutomationTrigger
    action: AutomationAction
    conditions: AutomationConditions = AutomationConditions()
    max_actions: int = Field(default=5, ge=1, le=50)
    dry_run: bool = False


class AutomationGuard(BaseModel):
    """État du garde-fou (services/automation_guard.py) : part de la
    bibliothèque tolérée d'un scan à l'autre, et pause éventuelle."""

    percent: int
    min_percent: int
    # Faux quand aucune automatisation activée ne porte sur les orphelins, les
    # doublons ou les torrents non hardlinkés : le garde-fou n'a alors rien à
    # protéger et l'interface ne l'affiche pas.
    active: bool = False
    paused: bool
    paused_at: datetime | None = None
    # Motif de la pause, tel que détecté par le scan (le libellé est traduit
    # par l'interface) : statut concerné et compteurs avant/après.
    status: str | None = None
    previous: int | None = None
    current: int | None = None
    total: int | None = None
    changed_percent: int | None = None


class AutomationGuardWrite(BaseModel):
    percent: int = Field(ge=5, le=100)


class AutomationStep(BaseModel):
    label: str
    success: bool
    error: str | None = None


class AutomationRunResult(BaseModel):
    automation_id: int
    name: str
    # Médias correspondant à la règle, et ceux réellement traités (plafond).
    matched: int
    executed: int
    dry_run: bool
    freed_bytes: int
    steps: list[AutomationStep]
