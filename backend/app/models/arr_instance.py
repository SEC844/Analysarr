from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ArrInstance(SQLModel, table=True):
    """Instance Sonarr/Radarr SUPPLÉMENTAIRE (ex : un Radarr dédié à la 4K).

    L'instance principale reste dans `Settings` (sonarr_*/radarr_*) : les
    configurations existantes et l'assistant ne changent pas. Configuration
    utilisateur : cette table n'est jamais supprimée avec le cache média (voir
    database.py)."""

    id: int | None = Field(default=None, primary_key=True)
    kind: str  # "sonarr" | "radarr"
    name: str
    url: str
    # Secret : jamais renvoyé au navigateur (voir routers/settings.py).
    api_key: str

    created_at: datetime = Field(default_factory=_utcnow)
