from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session

from app.database import get_session
from app.models.ignore import IgnoreRule
from app.models.media import Media
from app.schemas.ignores import IgnoreCreate, IgnoreRuleRead
from app.services.ignore_rules import IgnoreError, create_ignores, delete_ignore, list_ignores

router = APIRouter()


@router.get("", response_model=list[IgnoreRuleRead])
def get_ignores(session: Session = Depends(get_session)) -> list[IgnoreRuleRead]:
    return list_ignores(session)


@router.post("", status_code=204)
def post_ignore(payload: IgnoreCreate, session: Session = Depends(get_session)) -> Response:
    media = session.get(Media, payload.media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    try:
        create_ignores(session, media, payload)
    except IgnoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(status_code=204)


@router.delete("/{rule_id}", status_code=204)
def remove_ignore(rule_id: int, session: Session = Depends(get_session)) -> Response:
    rule = session.get(IgnoreRule, rule_id)
    if rule is None:
        raise HTTPException(404, "Élément ignoré introuvable.")
    delete_ignore(session, rule)
    return Response(status_code=204)
