from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

AutomationTrigger = Literal[
    "orphan_detected",
    "duplicate_detected",
    "non_hardlink_detected",
    "import_failed_detected",
    "stalled_download_detected",
]
AutomationAction = Literal["cleanup", "repair_hardlinks", "cross_seed_search", "retry_import", "notify_only"]


class AutomationConditions(BaseModel):
    """Toutes optionnelles : une condition absente ne filtre rien. Elles
    s'appliquent au média ET aux torrents concernés par l'action."""

    # Vide = films et séries.
    media_types: list[Literal["movie", "series"]] = []
    # Ancienneté minimale du seed, en jours, pour CHAQUE torrent concerné.
    min_seed_days: Optional[int] = Field(default=None, ge=0, le=3650)
    # Ratio minimal de CHAQUE torrent concerné.
    min_ratio: Optional[float] = Field(default=None, ge=0, le=1000)
    # Espace récupérable minimal du média.
    min_reclaimable_bytes: Optional[int] = Field(default=None, ge=0)


class AutomationRead(BaseModel):
    id: int
    name: str
    enabled: bool
    trigger: AutomationTrigger
    action: AutomationAction
    conditions: AutomationConditions
    max_actions: int
    dry_run: bool
    last_run_at: Optional[datetime]
    last_run_count: int


class AutomationWrite(BaseModel):
    name: str = Field(max_length=40)
    enabled: bool = True
    trigger: AutomationTrigger
    action: AutomationAction
    conditions: AutomationConditions = AutomationConditions()
    max_actions: int = Field(default=5, ge=1, le=50)
    dry_run: bool = False


class AutomationStep(BaseModel):
    label: str
    success: bool
    error: Optional[str] = None


class AutomationRunResult(BaseModel):
    automation_id: int
    name: str
    # Médias correspondant à la règle, et ceux réellement traités (plafond).
    matched: int
    executed: int
    dry_run: bool
    freed_bytes: int
    steps: list[AutomationStep]
