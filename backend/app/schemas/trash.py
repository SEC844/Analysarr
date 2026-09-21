from datetime import datetime

from pydantic import BaseModel, Field


class TrashEntryRead(BaseModel):
    id: int
    deleted_at: datetime
    original_path: str
    size: int
    media_title: str
    action: str
    # Faux si le fichier n'est plus dans la corbeille (retiré à la main).
    available: bool


class TrashSettings(BaseModel):
    enabled: bool
    retention_days: int
    min_days: int
    max_days: int


class TrashSettingsWrite(BaseModel):
    enabled: bool
    retention_days: int = Field(ge=1, le=90)
