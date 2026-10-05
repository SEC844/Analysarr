from datetime import UTC, datetime

from sqlmodel import Field, SQLModel, UniqueConstraint


def _utcnow() -> datetime:
    # Naïf (UTC), comme le reste des tables : SQLite relit sans fuseau.
    return datetime.now(UTC).replace(tzinfo=None)


class ArrWebhook(SQLModel, table=True):
    """Webhook qu'Analysarr a créé dans une instance Sonarr/Radarr (temps
    réel, voir services/realtime/webhooks.py). Table de CONFIGURATION : jamais
    vidée avec le cache média.

    Seule l'empreinte sha256 du secret est conservée : Sonarr/Radarr le
    présente à chaque appel (authentification Basic), Analysarr compare les
    empreintes en temps constant. Un secret perdu se remplace en rebranchant."""

    __table_args__ = (UniqueConstraint("service", "instance_id"),)

    id: int | None = Field(default=None, primary_key=True)
    # sonarr | radarr
    service: str
    # 0 = instance principale (réglages), sinon ArrInstance.id.
    instance_id: int = 0
    # Identifiant de la notification côté Sonarr/Radarr (None tant que la
    # création n'a pas abouti : Sonarr/Radarr teste le webhook AVANT de
    # répondre, le secret doit donc déjà être accepté).
    notification_id: int | None = None
    secret_hash: str
    # Rebranchement en cours : Sonarr/Radarr teste le NOUVEAU secret pendant
    # la création, alors que l'ancien reste le bon tant qu'elle n'a pas
    # abouti. Les deux sont acceptés le temps de l'opération ; un échec
    # n'invalide jamais le webhook qui fonctionnait.
    pending_secret_hash: str | None = None
    # Adresse donnée à Sonarr/Radarr (sans le secret).
    url: str
    created_at: datetime = Field(default_factory=_utcnow)
