"""Historique de la bibliothèque, en lecture (voir services/library_history.py).
Routes synchrones : FastAPI les exécute hors de la boucle asyncio."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.database import get_session
from app.models.library_snapshot import LibrarySnapshot
from app.models.settings import Settings
from app.schemas.library import (
    DiskForecastRead,
    DiskPointRead,
    DiskUsageRead,
    FillRead,
    ForecastRead,
    LibraryGrowthRead,
    LibraryHistorySettings,
    LibraryHistorySettingsWrite,
    LibrarySnapshotRead,
    ProjectionPointRead,
    TrendRead,
)
from app.services.forecast import (
    HORIZON_DAYS,
    MIN_HISTORY_DAYS,
    WINDOW_DAYS,
    DiskForecast,
    FillEstimate,
    Forecast,
    Trend,
    load_forecast,
)
from app.services.library_history import (
    DEFAULT_RETENTION_DAYS,
    MAX_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    disks_of,
    history,
    retention_days,
)

router = APIRouter()


def _disks(snapshot: LibrarySnapshot) -> list[DiskUsageRead]:
    numbers: dict[int, int] = {}
    reads = []
    for usage in disks_of(snapshot):
        disk = None if usage.device is None else numbers.setdefault(usage.device, len(numbers))
        reads.append(
            DiskUsageRead(
                role=usage.role,
                path=usage.path,
                available=usage.available,
                total=usage.total,
                free=usage.free,
                disk=disk,
            )
        )
    return reads


def _to_read(snapshot: LibrarySnapshot) -> LibrarySnapshotRead:
    return LibrarySnapshotRead(
        day=snapshot.day,
        taken_at=snapshot.taken_at,
        media_count=snapshot.media_count,
        movie_count=snapshot.movie_count,
        series_count=snapshot.series_count,
        total_size=snapshot.total_size,
        movie_size=snapshot.movie_size,
        series_size=snapshot.series_size,
        disks=_disks(snapshot),
    )


def _settings(session: Session) -> LibraryHistorySettings:
    return LibraryHistorySettings(
        retention_days=retention_days(session.get(Settings, 1)),
        min_days=MIN_RETENTION_DAYS,
        max_days=MAX_RETENTION_DAYS,
    )


@router.get("/history", response_model=list[LibrarySnapshotRead])
def read_history(
    days: int = Query(DEFAULT_RETENTION_DAYS, ge=1, le=MAX_RETENTION_DAYS),
    session: Session = Depends(get_session),
) -> list[LibrarySnapshotRead]:
    return [_to_read(snapshot) for snapshot in history(session, days)]


@router.get("/history/settings", response_model=LibraryHistorySettings)
def read_settings(session: Session = Depends(get_session)) -> LibraryHistorySettings:
    return _settings(session)


@router.put("/history/settings", response_model=LibraryHistorySettings)
def update_settings(
    payload: LibraryHistorySettingsWrite, session: Session = Depends(get_session)
) -> LibraryHistorySettings:
    """Une rétention raccourcie ne supprime rien sur-le-champ : la purge a
    lieu à la photographie suivante, ce qui laisse le temps de revenir sur une
    erreur de saisie."""
    settings = session.get(Settings, 1) or Settings(id=1)
    settings.library_history_retention_days = payload.retention_days
    session.add(settings)
    session.commit()
    return _settings(session)


def trend_read(trend: Trend | None) -> TrendRead | None:
    return None if trend is None else TrendRead(per_day=trend.per_day, low=trend.low, high=trend.high)


def _after(day: date, days: float | None) -> date | None:
    return None if days is None else day + timedelta(days=round(days))


def _fill_read(fill: FillEstimate | None, day: date) -> FillRead | None:
    if fill is None:
        return None
    return FillRead(
        earliest_days=fill.earliest_days,
        latest_days=fill.latest_days,
        earliest_date=day + timedelta(days=round(fill.earliest_days)),
        latest_date=_after(day, fill.latest_days),
    )


def _disk_forecast_read(disk: DiskForecast, day: date) -> DiskForecastRead:
    return DiskForecastRead(
        key=disk.key,
        roles=list(disk.roles),
        paths=disk.paths,
        available=disk.available,
        total=disk.total,
        used=disk.used,
        free=disk.free,
        history=[DiskPointRead(day=p.day, used=p.used, total=p.total) for p in disk.history],
        history_days=disk.history_days,
        trend=trend_read(disk.trend),
        fill=_fill_read(disk.fill, day),
        projection=[ProjectionPointRead(day=p.day, used=p.used, low=p.low, high=p.high) for p in disk.projection],
    )


def forecast_read(forecast: Forecast) -> ForecastRead:
    day = forecast.latest_day
    return ForecastRead(
        window_days=WINDOW_DAYS,
        min_history_days=MIN_HISTORY_DAYS,
        horizon_days=HORIZON_DAYS,
        history_days=forecast.history_days,
        enough_history=forecast.enough_history,
        latest_day=day,
        library=(
            None
            if forecast.library_size is None
            else LibraryGrowthRead(size=forecast.library_size, trend=trend_read(forecast.library_trend))
        ),
        disks=[_disk_forecast_read(disk, day) for disk in forecast.disks] if day else [],
    )


@router.get("/forecast", response_model=ForecastRead)
def read_forecast(session: Session = Depends(get_session)) -> ForecastRead:
    """Prévisions d'espace disque (voir services/forecast.py) : une requête
    sur au plus 90 lignes, calcul en mémoire."""
    return forecast_read(load_forecast(session))
