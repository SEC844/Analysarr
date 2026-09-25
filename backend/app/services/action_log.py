"""Journal des actions (Réglages → Historique) : qui a été supprimé, réparé ou
cherché, quand, et avec quel résultat. Taille bornée pour ne jamais faire
grossir la base indéfiniment."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlmodel import Session, col, delete, select

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
    year: int | None = None
    # Jaquette jointe aux notifications (cache disque ou serveur multimédia).
    emby_item_id: str | None = None
    poster_image_tag: str | None = None

    @classmethod
    def of(cls, media: Media) -> "MediaRef":
        return cls(
            id=media.id,
            title=media.title,
            media_type=media.media_type.value,
            year=media.year,
            emby_item_id=media.emby_item_id,
            poster_image_tag=media.poster_image_tag,
        )


def record_action(
    session: Session,
    action: str,
    media: MediaRef,
    steps: Sequence[Step],
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

    stale = session.exec(select(ActionLog.id).order_by(col(ActionLog.id).desc()).offset(MAX_ENTRIES)).all()
    if stale:
        session.exec(delete(ActionLog).where(col(ActionLog.id).in_(stale)))
        session.commit()
    session.refresh(entry)
    return entry
