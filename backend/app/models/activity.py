from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ActionLog(SQLModel, table=True):
    """Journal des actions effectuées depuis Analysarr (suppressions,
    réparations, recherches cross-seed). Contrairement au cache média, cette
    table n'est JAMAIS vidée par un scan : c'est la trace de ce qui a été fait,
    quand, sur quel média. Taille bornée (voir services/action_log.py)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow, index=True)

    # delete_selection | cascade_delete | hardlink_repair | cross_seed_search
    action: str
    # Copiés au moment de l'action : la fiche média peut avoir disparu depuis.
    media_title: str
    media_type: Optional[str] = None
    media_id: Optional[int] = None

    success_count: int = 0
    failure_count: int = 0
    # Espace disque libéré estimé (suppressions), en octets.
    freed_bytes: Optional[int] = None
    # Détail des étapes : liste JSON [{"label", "success", "error"}].
    details_json: str = "[]"
