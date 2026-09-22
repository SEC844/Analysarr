from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.media import DeleteStepResult


class TrashItemRead(BaseModel):
    """Élément d'une suppression mise de côté (fichier de bibliothèque ou
    données d'un torrent)."""

    kind: str
    label: str
    size: int
    original_path: Optional[str] = None
    # Faux si l'élément a disparu de la corbeille (retiré à la main).
    available: bool


class TrashActionRead(BaseModel):
    """Une suppression entière : c'est l'unité de restauration."""

    id: int
    created_at: datetime
    action: str
    media_title: str
    media_type: Optional[str] = None
    size: int
    items: list[TrashItemRead]
    # Ce que la restauration remettra en plus des fichiers.
    restores_arr: bool
    restores_seer: bool
    # Faux dès qu'un élément manque : restaurer ne rendrait qu'une partie.
    restorable: bool


class TrashRestoreResult(BaseModel):
    steps: list[DeleteStepResult]
    # Vrai si tout a été remis en place (l'action quitte alors la corbeille).
    complete: bool
    actions: list[TrashActionRead]


class TrashSettings(BaseModel):
    enabled: bool
    retention_days: int
    min_days: int
    max_days: int


class TrashSettingsWrite(BaseModel):
    enabled: bool
    retention_days: int = Field(ge=1, le=90)
