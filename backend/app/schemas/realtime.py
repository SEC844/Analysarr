from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ArrService = Literal["sonarr", "radarr"]

# `connected` : branché et à jour ; `pending` : pas encore branché (essai en
# cours ou à venir) ; `error` : dernier essai refusé, branchement ou retrait
# (message dans `error`) ; `no_address` : adresse d'Analysarr inconnue ;
# `removing` : webhooks désactivés, retrait en cours ; `disabled` : webhooks
# désactivés, rien chez Sonarr/Radarr.
WebhookState = Literal["connected", "pending", "error", "no_address", "removing", "disabled"]
SourceState = Literal["active", "waiting", "error"]


class WebhookRead(BaseModel):
    service: ArrService
    # 0 = instance principale.
    instance_id: int
    name: str
    state: WebhookState
    error: str | None = None
    url: str | None = None
    # Suivi en lecture seule de l'instance (historique, file d'attente) :
    # None quand le temps réel est désactivé.
    polling: SourceState | None = None
    polling_error: str | None = None


class WebhooksRead(BaseModel):
    """Webhooks Sonarr/Radarr, branchés automatiquement sauf refus, et
    l'adresse d'Analysarr qu'ils utilisent."""

    analysarr_url: str
    realtime_enabled: bool
    # Création automatique des webhooks autorisée.
    enabled: bool
    webhooks: list[WebhookRead]


class WebhooksEnabledWrite(BaseModel):
    enabled: bool


class AddressWrite(BaseModel):
    """Adresse d'Analysarr vue depuis Sonarr/Radarr (revérifiée côté serveur).
    `detected` : proposée par le navigateur, enregistrée seulement si aucune
    adresse ne l'est encore (jamais d'écrasement d'une saisie)."""

    analysarr_url: str = Field(min_length=1, max_length=500)
    detected: bool = False


class SourceStatusRead(BaseModel):
    key: str
    kind: str
    state: SourceState
    last_event_at: datetime | None
    last_check_at: datetime | None
    error: str | None


class RealtimeStatusRead(BaseModel):
    # Temps réel activé (sinon : scans uniquement).
    enabled: bool
    # Activé ET au moins un service suivi.
    active: bool
    # Adresse d'Analysarr connue (sinon le navigateur propose la sienne).
    address_set: bool
    sources: list[SourceStatusRead]
