from typing import Literal, Optional

from pydantic import BaseModel

ServiceName = Literal["emby", "sonarr", "radarr", "qbittorrent", "cross_seed"]


class ServiceApiKeyRead(BaseModel):
    url: Optional[str] = None
    api_key_set: bool = False


class QbittorrentRead(BaseModel):
    url: Optional[str] = None
    username: Optional[str] = None
    password_set: bool = False


class PathsRead(BaseModel):
    emby_library_path: Optional[str] = None
    qbittorrent_download_path: Optional[str] = None


class CrossSeedRead(BaseModel):
    enabled: bool = False
    url: Optional[str] = None
    api_key_set: bool = False
    library_path: Optional[str] = None


class ScheduleRead(BaseModel):
    enabled: bool = False
    interval_minutes: Optional[int] = None


class SettingsRead(BaseModel):
    configured: bool
    emby: ServiceApiKeyRead
    sonarr: ServiceApiKeyRead
    radarr: ServiceApiKeyRead
    qbittorrent: QbittorrentRead
    paths: PathsRead
    cross_seed: CrossSeedRead
    schedule: ScheduleRead


class SettingsWrite(BaseModel):
    """Toutes les clés/mots de passe sont optionnels : une valeur absente ou
    vide conserve la valeur déjà enregistrée en base (évite de l'écraser
    quand l'utilisateur ré-enregistre le formulaire sans la ressaisir)."""

    emby_url: Optional[str] = None
    emby_api_key: Optional[str] = None

    sonarr_url: Optional[str] = None
    sonarr_api_key: Optional[str] = None

    radarr_url: Optional[str] = None
    radarr_api_key: Optional[str] = None

    qbittorrent_url: Optional[str] = None
    qbittorrent_username: Optional[str] = None
    qbittorrent_password: Optional[str] = None

    emby_library_path: Optional[str] = None
    qbittorrent_download_path: Optional[str] = None

    cross_seed_enabled: bool = False
    cross_seed_url: Optional[str] = None
    cross_seed_api_key: Optional[str] = None
    cross_seed_library_path: Optional[str] = None

    scan_schedule_enabled: bool = False
    scan_schedule_interval_minutes: Optional[int] = None


class ConnectionTestRequest(BaseModel):
    url: Optional[str] = None
    api_key: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


class ConnectionTestResult(BaseModel):
    success: bool
    message: str


class BrowseEntry(BaseModel):
    name: str
    path: str


class BrowseResult(BaseModel):
    path: str
    parent: Optional[str]
    directories: list[BrowseEntry]
