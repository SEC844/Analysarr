from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    # Naïf (UTC), comme le reste des tables : SQLite relit sans fuseau.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TrashEntry(SQLModel, table=True):
    """Fichier déplacé en corbeille par Analysarr (services/trash.py).
    Table de données, jamais vidée avec le cache média : elle seule sait où
    retrouver un fichier et à quel chemin le remettre."""

    id: Optional[int] = Field(default=None, primary_key=True)
    deleted_at: datetime = Field(default_factory=_utcnow, index=True)

    original_path: str
    trashed_path: str
    size: int = 0
    # Média concerné au moment de la suppression (sa fiche peut avoir disparu).
    media_title: str = ""
    # delete_selection | cascade_delete
    action: str = "delete_selection"
