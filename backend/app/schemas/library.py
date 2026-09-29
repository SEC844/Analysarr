from datetime import date, datetime

from pydantic import BaseModel, Field

from app.services.library_history import MAX_RETENTION_DAYS, MIN_RETENTION_DAYS


class DiskUsageRead(BaseModel):
    role: str  # library | downloads
    path: str
    available: bool
    total: int | None = None
    free: int | None = None
    # Numéro de disque, propre à une photographie : deux racines portées par le
    # même système de fichiers ont le même numéro (ne compter leur espace
    # qu'une fois). Jamais l'identifiant brut du système, qu'un nombre
    # JavaScript ne représenterait pas toujours exactement.
    disk: int | None = None


class LibrarySnapshotRead(BaseModel):
    day: date
    taken_at: datetime
    media_count: int
    movie_count: int
    series_count: int
    total_size: int
    movie_size: int
    series_size: int
    disks: list[DiskUsageRead]


class LibraryHistorySettings(BaseModel):
    retention_days: int
    min_days: int
    max_days: int


class LibraryHistorySettingsWrite(BaseModel):
    retention_days: int = Field(ge=MIN_RETENTION_DAYS, le=MAX_RETENTION_DAYS)
