from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.media import DeleteStepResult


class TrashItemRead(BaseModel):
    """Élément d'une suppression mise de côté (fichier de bibliothèque ou
    données d'un torrent)."""

    kind: str
    label: str
    size: int
    original_path: str | None = None
    # Faux si l'élément a disparu de la corbeille (retiré à la main).
    available: bool


class TrashActionRead(BaseModel):
    """Une suppression entière : c'est l'unité de restauration."""

    id: int
    created_at: datetime
    action: str
    media_title: str
    media_type: str | None = None
    size: int
    items: list[TrashItemRead]
    # Vrai si le suivi Sonarr/Radarr sera recréé par la restauration.
    restores_arr: bool
    # Faux dès qu'un élément manque : restaurer ne rendrait qu'une partie.
    restorable: bool


class TrashRestoreResult(BaseModel):
    steps: list[DeleteStepResult]
    # Vrai si tout a été remis en place (l'action quitte alors la corbeille).
    complete: bool
    # Vrai si une analyse a été lancée pour faire réapparaître le média.
    rescan_started: bool = False
    actions: list[TrashActionRead]


class TrashSettings(BaseModel):
    enabled: bool
    retention_days: int
    min_days: int
    max_days: int


class TrashSettingsWrite(BaseModel):
    enabled: bool
    retention_days: int = Field(ge=1, le=90)
