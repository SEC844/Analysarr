from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ArrService = Literal["sonarr", "radarr"]

# `connected` : branché et à jour ; `pending` : pas encore branché (essai en
# cours ou à venir) ; `error` : dernier essai refusé (message dans `error`) ;
# `no_address` : adresse d'Analysarr inconnue.
WebhookState = Literal["connected", "pending", "error", "no_address"]


class WebhookRead(BaseModel):
    service: ArrService
    # 0 = instance principale.
    instance_id: int
    name: str
    state: WebhookState
    error: str | None = None
    url: str | None = None


class WebhooksRead(BaseModel):
    """Webhooks Sonarr/Radarr, branchés automatiquement, et l'adresse
    d'Analysarr qu'ils utilisent."""

    analysarr_url: str
    webhooks: list[WebhookRead]


class AddressWrite(BaseModel):
    """Adresse d'Analysarr vue depuis Sonarr/Radarr (revérifiée côté serveur).
    `detected` : proposée par le navigateur, enregistrée seulement si aucune
    adresse ne l'est encore (jamais d'écrasement d'une saisie)."""

    analysarr_url: str = Field(min_length=1, max_length=500)
    detected: bool = False


class SourceStatusRead(BaseModel):
    key: str
    kind: str
    state: Literal["active", "waiting", "error"]
    last_event_at: datetime | None
    last_check_at: datetime | None
    error: str | None


class RealtimeStatusRead(BaseModel):
    # Au moins un service suivi en temps réel.
    active: bool
    # Adresse d'Analysarr connue (sinon le navigateur propose la sienne).
    address_set: bool
    sources: list[SourceStatusRead]
