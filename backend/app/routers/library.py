"""Historique de la bibliothèque, en lecture (voir services/library_history.py).
Routes synchrones : FastAPI les exécute hors de la boucle asyncio."""

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.database import get_session
from app.models.library_snapshot import LibrarySnapshot
from app.models.settings import Settings
from app.schemas.library import (
    DiskUsageRead,
    LibraryHistorySettings,
    LibraryHistorySettingsWrite,
    LibrarySnapshotRead,
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
