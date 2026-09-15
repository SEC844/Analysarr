"""Journal des actions (Réglages → Historique) : qui a été supprimé, réparé ou
cherché, quand, et avec quel résultat. Taille bornée pour ne jamais faire
grossir la base indéfiniment."""

import json
from dataclasses import dataclass
from typing import Protocol

from sqlmodel import Session, delete, select

from app.models.activity import ActionLog
from app.models.media import Media

MAX_ENTRIES = 1000


class Step(Protocol):
    label: str
    success: bool
    error: str | None


@dataclass(frozen=True)
class MediaRef:
    """Identité du média copiée AVANT l'action : une suppression complète
    efface la fiche, dont les attributs ne sont alors plus lisibles."""

    id: int | None
    title: str
    media_type: str

    @classmethod
    def of(cls, media: Media) -> "MediaRef":
        return cls(id=media.id, title=media.title, media_type=media.media_type.value)


def record_action(
    session: Session,
    action: str,
    media: MediaRef,
    steps: list[Step],
    freed_bytes: int | None = None,
) -> ActionLog:
    entry = ActionLog(
        action=action,
        media_title=media.title,
        media_type=media.media_type,
        media_id=media.id,
        success_count=sum(1 for s in steps if s.success),
        failure_count=sum(1 for s in steps if not s.success),
        freed_bytes=freed_bytes,
        details_json=json.dumps(
            [{"label": s.label, "success": s.success, "error": s.error} for s in steps], ensure_ascii=False
        ),
    )
    session.add(entry)
    session.commit()

    stale = session.exec(select(ActionLog.id).order_by(ActionLog.id.desc()).offset(MAX_ENTRIES)).all()
    if stale:
        session.exec(delete(ActionLog).where(ActionLog.id.in_(stale)))
        session.commit()
    session.refresh(entry)
    return entry
