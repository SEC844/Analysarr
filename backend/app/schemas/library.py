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


# --- Prévisions (services/forecast.py) ---------------------------------------


class TrendRead(BaseModel):
    """Croissance en octets par jour : estimation centrale et fourchette
    (intervalle de confiance à 90 % de la pente)."""

    per_day: float
    low: float
    high: float


class FillRead(BaseModel):
    """Disque plein dans `earliest_days` jours au plus tôt (pente haute) et
    `latest_days` au plus tard (pente basse ; None = pas de date au plus
    tard). Dates correspondantes, à partir de la dernière mesure."""

    earliest_days: float
    latest_days: float | None
    earliest_date: date
    latest_date: date | None


class DiskPointRead(BaseModel):
    day: date
    used: int
    total: int


class ProjectionPointRead(BaseModel):
    day: date
    used: int
    low: int
    high: int


class DiskForecastRead(BaseModel):
    # Racines portées par ce disque, jointes par « + » (identifiant stable,
    # celui que le mode objectif de l'assistant reçoit).
    key: str
    roles: list[str]
    paths: list[str]
    available: bool
    total: int | None
    used: int | None
    free: int | None
    history: list[DiskPointRead]
    history_days: int
    trend: TrendRead | None
    fill: FillRead | None
    projection: list[ProjectionPointRead]


class LibraryGrowthRead(BaseModel):
    size: int
    trend: TrendRead | None


class ForecastRead(BaseModel):
    window_days: int
    min_history_days: int
    horizon_days: int
    # Jours mesurés dans la fenêtre de calcul (message honnête en dessous du
    # minimum).
    history_days: int
    enough_history: bool
    latest_day: date | None
    library: LibraryGrowthRead | None
    disks: list[DiskForecastRead]
