"""Corbeille : consultation, restauration, purge (voir services/trash.py)."""

import os

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.database import get_session
from app.models.settings import Settings
from app.models.trash import TrashEntry
from app.schemas.trash import TrashEntryRead, TrashSettings, TrashSettingsWrite
from app.services.trash import (
    MAX_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    entries,
    is_enabled,
    purge_entry,
    purge_expired,
    restore,
    retention_days,
)

router = APIRouter()


def _to_read(entry: TrashEntry) -> TrashEntryRead:
    return TrashEntryRead(
        id=entry.id,
        deleted_at=entry.deleted_at,
        original_path=entry.original_path,
        size=entry.size,
        media_title=entry.media_title,
        action=entry.action,
        # Un fichier retiré à la main de la corbeille ne doit pas se présenter
        # comme restaurable.
        available=os.path.lexists(entry.trashed_path),
    )


def _get(entry_id: int, session: Session) -> TrashEntry:
    entry = session.get(TrashEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Entrée introuvable dans la corbeille.")
    return entry


@router.get("/settings", response_model=TrashSettings)
def read_settings(session: Session = Depends(get_session)) -> TrashSettings:
    settings = session.get(Settings, 1)
    return TrashSettings(
        enabled=is_enabled(settings),
        retention_days=retention_days(settings),
        min_days=MIN_RETENTION_DAYS,
        max_days=MAX_RETENTION_DAYS,
    )


@router.put("/settings", response_model=TrashSettings)
def update_settings(payload: TrashSettingsWrite, session: Session = Depends(get_session)) -> TrashSettings:
    settings = session.get(Settings, 1) or Settings(id=1)
    settings.trash_enabled = payload.enabled
    settings.trash_retention_days = payload.retention_days
    session.add(settings)
    session.commit()
    return TrashSettings(
        enabled=settings.trash_enabled,
        retention_days=retention_days(settings),
        min_days=MIN_RETENTION_DAYS,
        max_days=MAX_RETENTION_DAYS,
    )


@router.get("", response_model=list[TrashEntryRead])
def list_entries(session: Session = Depends(get_session)) -> list[TrashEntryRead]:
    return [_to_read(entry) for entry in entries(session)]


@router.post("/{entry_id}/restore", response_model=list[TrashEntryRead])
def restore_entry(entry_id: int, session: Session = Depends(get_session)) -> list[TrashEntryRead]:
    entry = _get(entry_id, session)
    try:
        restore(session, entry)
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(400, f"Restauration impossible : {exc}") from exc
    return [_to_read(row) for row in entries(session)]


@router.delete("/{entry_id}", status_code=204)
def delete_entry(entry_id: int, session: Session = Depends(get_session)) -> None:
    purge_entry(session, _get(entry_id, session))


@router.delete("", status_code=204)
def empty_trash(session: Session = Depends(get_session)) -> None:
    for entry in entries(session):
        purge_entry(session, entry)


@router.post("/purge", response_model=list[TrashEntryRead])
def purge_now(session: Session = Depends(get_session)) -> list[TrashEntryRead]:
    """Applique la rétention immédiatement (elle s'applique aussi toute seule,
    voir services/scheduler.py)."""
    purge_expired(session, session.get(Settings, 1))
    return [_to_read(entry) for entry in entries(session)]
