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
from app.services.action_log import MediaRef, record_action
from app.services.scan import launch_scan
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
    # Copiés avant : l'action disparaît de la corbeille dès que tout est rendu.
    ref = MediaRef(id=None, title=action.media_title, media_type=action.media_type or "movie")
    restores_arr = bool(action.arr_payload)

    steps, complete = await restore_action(session, session.get(Settings, 1), action)
    record_action(session, "trash_restore", ref, steps)

    # Le média restauré doit réapparaître sans que l'utilisateur relance un
    # scan complet : une analyse du service concerné suffit (elle seule peut
    # recréer une fiche média retirée de la bibliothèque).
    rescan = complete and launch_scan(scope=_rescan_scope(ref.media_type, restores_arr))
    return TrashRestoreResult(steps=steps, complete=complete, rescan_started=rescan, actions=_list(session))


def _rescan_scope(media_type: str | None, restores_arr: bool) -> str:
    if not restores_arr:
        return "torrents"  # seuls des torrents sont revenus : rien à redemander à Sonarr/Radarr
    return "radarr" if media_type == "movie" else "sonarr"


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
