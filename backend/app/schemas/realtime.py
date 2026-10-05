from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ArrService = Literal["sonarr", "radarr"]

DEBOUNCE_BOUNDS = (1, 60)
TORRENT_INTERVAL_BOUNDS = (2, 300)
MEDIA_SERVER_INTERVAL_BOUNDS = (3, 300)


class WebhookRead(BaseModel):
    service: ArrService
    # 0 = instance principale.
    instance_id: int
    name: str
    connected: bool
    url: str | None = None


class RealtimeSettingsRead(BaseModel):
    debounce_seconds: int
    torrents_enabled: bool
    torrents_interval: int
    torrents_available: bool
    media_server_enabled: bool
    media_server_interval: int
    media_server_available: bool
    analysarr_url: str
    webhooks: list[WebhookRead]
    debounce_bounds: tuple[int, int] = DEBOUNCE_BOUNDS
    torrent_interval_bounds: tuple[int, int] = TORRENT_INTERVAL_BOUNDS
    media_server_interval_bounds: tuple[int, int] = MEDIA_SERVER_INTERVAL_BOUNDS


class RealtimeSettingsWrite(BaseModel):
    debounce_seconds: int = Field(ge=DEBOUNCE_BOUNDS[0], le=DEBOUNCE_BOUNDS[1])
    torrents_enabled: bool
    torrents_interval: int = Field(ge=TORRENT_INTERVAL_BOUNDS[0], le=TORRENT_INTERVAL_BOUNDS[1])
    media_server_enabled: bool
    media_server_interval: int = Field(ge=MEDIA_SERVER_INTERVAL_BOUNDS[0], le=MEDIA_SERVER_INTERVAL_BOUNDS[1])


class WebhookRequest(BaseModel):
    """Adresse d'Analysarr vue depuis Sonarr/Radarr (revérifiée côté serveur)."""

    analysarr_url: str = Field(min_length=1, max_length=500)


class WebhookPreviewRead(BaseModel):
    name: str
    url: str
    events: list[str]


class SourceStatusRead(BaseModel):
    key: str
    kind: str
    state: Literal["active", "waiting", "error"]
    last_event_at: datetime | None
    last_check_at: datetime | None
    error: str | None


class NightlyScanRequest(BaseModel):
    hour: int = Field(default=4, ge=0, le=23)


class ReconciliationRead(BaseModel):
    """Scan complet planifié : le filet de sécurité du temps réel."""

    enabled: bool
    mode: Literal["interval", "nightly"]
    interval_minutes: int | None
    nightly_hour: int


class RealtimeStatusRead(BaseModel):
    # Au moins une source branchée.
    active: bool
    sources: list[SourceStatusRead]
    reconciliation: ReconciliationRead
