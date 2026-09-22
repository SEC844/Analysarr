"""Corbeille : consultation par action, restauration, purge (voir
services/trash.py)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.database import get_session
from app.models.settings import Settings
from app.models.trash import TrashAction
from app.schemas.trash import (
    TrashActionRead,
    TrashItemRead,
    TrashRestoreResult,
    TrashSettings,
    TrashSettingsWrite,
)
from app.services.trash import (
    MAX_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    actions,
    is_enabled,
    item_available,
    items_of,
    purge_action,
    purge_expired,
    restore_action,
    retention_days,
)

router = APIRouter()


def _to_read(session: Session, action: TrashAction) -> TrashActionRead:
    items = items_of(session, action)
    reads = [
        TrashItemRead(
            kind=item.kind,
            label=item.label,
            size=item.size,
            original_path=item.original_path,
            available=item_available(item),
        )
        for item in items
    ]
    return TrashActionRead(
        id=action.id,
        created_at=action.created_at,
        action=action.action,
        media_title=action.media_title,
        media_type=action.media_type,
        size=sum(item.size for item in items),
        items=reads,
        restores_arr=bool(action.arr_payload),
        restores_seer=bool(action.seer_payload),
        restorable=all(read.available for read in reads),
    )


def _list(session: Session) -> list[TrashActionRead]:
    return [_to_read(session, action) for action in actions(session)]


def _get(action_id: int, session: Session) -> TrashAction:
    action = session.get(TrashAction, action_id)
    if action is None:
        raise HTTPException(404, "Suppression introuvable dans la corbeille.")
    return action


def _settings(session: Session) -> TrashSettings:
    settings = session.get(Settings, 1)
    return TrashSettings(
        enabled=is_enabled(settings),
        retention_days=retention_days(settings),
        min_days=MIN_RETENTION_DAYS,
        max_days=MAX_RETENTION_DAYS,
    )


@router.get("/settings", response_model=TrashSettings)
def read_settings(session: Session = Depends(get_session)) -> TrashSettings:
    return _settings(session)


@router.put("/settings", response_model=TrashSettings)
def update_settings(payload: TrashSettingsWrite, session: Session = Depends(get_session)) -> TrashSettings:
    settings = session.get(Settings, 1) or Settings(id=1)
    settings.trash_enabled = payload.enabled
    settings.trash_retention_days = payload.retention_days
    session.add(settings)
    session.commit()
    return _settings(session)


@router.get("", response_model=list[TrashActionRead])
def list_actions(session: Session = Depends(get_session)) -> list[TrashActionRead]:
    return _list(session)


@router.post("/{action_id}/restore", response_model=TrashRestoreResult)
async def restore(action_id: int, session: Session = Depends(get_session)) -> TrashRestoreResult:
    """Restauration d'un bloc : fichiers, torrents, suivi Sonarr/Radarr et
    demande Seer. Une action dont une étape échoue reste dans la corbeille."""
    action = _get(action_id, session)
    steps, complete = await restore_action(session, session.get(Settings, 1), action)
    return TrashRestoreResult(steps=steps, complete=complete, actions=_list(session))


@router.delete("/{action_id}", status_code=204)
def delete_action(action_id: int, session: Session = Depends(get_session)) -> None:
    purge_action(session, _get(action_id, session))


@router.delete("", status_code=204)
def empty_trash(session: Session = Depends(get_session)) -> None:
    for action in actions(session):
        purge_action(session, action)


@router.post("/purge", response_model=list[TrashActionRead])
def purge_now(session: Session = Depends(get_session)) -> list[TrashActionRead]:
    """Applique la rétention immédiatement (elle s'applique aussi toute seule,
    voir services/scheduler.py)."""
    purge_expired(session, session.get(Settings, 1))
    return _list(session)
