import json

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, col, delete, select

from app.database import get_session
from app.models.activity import ActionLog
from app.models.ids import row_id
from app.models.media import Media
from app.schemas.activity import ActionLogRead, ActionStepRead
from app.services.action_log import MAX_ENTRIES

router = APIRouter()


@router.get("", response_model=list[ActionLogRead])
def list_actions(
    limit: int = Query(200, ge=1, le=MAX_ENTRIES), session: Session = Depends(get_session)
) -> list[ActionLogRead]:
    entries = session.exec(select(ActionLog).order_by(col(ActionLog.id).desc()).limit(limit)).all()
    titles = dict(session.exec(select(Media.id, Media.title)).all())
    return [
        ActionLogRead(
            id=row_id(e),
            created_at=e.created_at,
            action=e.action,
            media_title=e.media_title,
            media_type=e.media_type,
            # Les ids média changent à chaque scan : un id encore présent peut
            # désigner un autre média, le lien n'est proposé que si le titre
            # correspond toujours.
            media_id=e.media_id if e.media_id is not None and titles.get(e.media_id) == e.media_title else None,
            success_count=e.success_count,
            failure_count=e.failure_count,
            freed_bytes=e.freed_bytes,
            details=[ActionStepRead(**step) for step in json.loads(e.details_json or "[]")],
        )
        for e in entries
    ]


@router.delete("", status_code=204)
def clear_actions(session: Session = Depends(get_session)) -> None:
    session.exec(delete(ActionLog))
    session.commit()
