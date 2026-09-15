from datetime import datetime

from pydantic import BaseModel


class ServiceStatusRead(BaseModel):
    service: str  # emby | sonarr | radarr | qbittorrent | cross_seed | seer
    # Nom affiché : « Emby »/« Jellyfin », nom de l'instance Sonarr/Radarr...
    name: str
    ok: bool
    message: str


class ServicesStatus(BaseModel):
    checked_at: datetime
    services: list[ServiceStatusRead]
