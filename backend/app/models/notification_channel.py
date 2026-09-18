from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NotificationChannel(SQLModel, table=True):
    """Un canal de notification : un webhook Discord, un sujet ntfy ou un
    serveur Gotify, avec SA propre liste d'événements. Plusieurs canaux du même
    type peuvent coexister (un webhook pour les scans, un autre pour les
    suppressions).

    Configuration utilisateur : cette table n'est jamais supprimée avec le
    cache média (voir database.py)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str  # discord | ntfy | gotify
    name: str
    # Secret pour Discord (URL du webhook) et ntfy (nom du sujet) ; simple
    # adresse de serveur pour Gotify. Jamais renvoyée telle quelle au
    # navigateur sauf pour Gotify (voir routers/notifications.py).
    url: str
    # Jeton d'accès : optionnel pour ntfy, obligatoire pour Gotify, inutile
    # pour Discord (le secret est dans l'URL).
    token: Optional[str] = None
    # Liste JSON d'événements (voir services/notifications.NOTIFICATION_EVENTS).
    events: str = "[]"
    enabled: bool = True

    created_at: datetime = Field(default_factory=_utcnow)
