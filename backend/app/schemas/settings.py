from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, StringConstraints

ServiceName = Literal["emby", "sonarr", "radarr", "qbittorrent", "cross_seed", "seer"]
MediaServer = Literal["emby", "jellyfin"]


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


class SeerRead(BaseModel):
    enabled: bool = False
    url: Optional[str] = None
    api_key_set: bool = False


class ScheduleRead(BaseModel):
    enabled: bool = False
    interval_minutes: Optional[int] = None


class WatchRead(BaseModel):
    excluded_emby_user_ids: list[str] = []


class NotificationsRead(BaseModel):
    # Secrets (URL de webhook Discord, URL de sujet ntfy, jetons) : seul le
    # fait qu'ils soient configurés est renvoyé.
    discord_set: bool = False
    ntfy_set: bool = False
    ntfy_token_set: bool = False
    gotify_url: Optional[str] = None
    gotify_token_set: bool = False
    on_scan: bool = False
    on_scan_failure: bool = True
    on_actions: bool = True


class SettingsRead(BaseModel):
    configured: bool
    media_server: MediaServer = "emby"
    watch: WatchRead
    emby: ServiceApiKeyRead
    sonarr: ServiceApiKeyRead
    radarr: ServiceApiKeyRead
    qbittorrent: QbittorrentRead
    paths: PathsRead
    cross_seed: CrossSeedRead
    seer: SeerRead
    schedule: ScheduleRead
    notifications: NotificationsRead = NotificationsRead()


class SettingsWrite(BaseModel):
    """Toutes les clés/mots de passe sont optionnels : une valeur absente ou
    vide conserve la valeur déjà enregistrée en base (évite de l'écraser
    quand l'utilisateur ré-enregistre le formulaire sans la ressaisir)."""

    media_server: MediaServer = "emby"
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

    seer_enabled: bool = False
    seer_url: Optional[str] = None
    seer_api_key: Optional[str] = None

    scan_schedule_enabled: bool = False
    scan_schedule_interval_minutes: Optional[int] = None

    # Notifications : un champ secret vide conserve la valeur enregistrée ;
    # `notify_clear` retire explicitement un canal.
    notify_discord_webhook: Optional[str] = None
    notify_ntfy_url: Optional[str] = None
    notify_ntfy_token: Optional[str] = None
    notify_gotify_url: Optional[str] = None
    notify_gotify_token: Optional[str] = None
    notify_clear: list[Literal["discord", "ntfy", "gotify"]] = []
    notify_on_scan: bool = False
    notify_on_scan_failure: bool = True
    notify_on_actions: bool = True

    # Identifiants Emby exclus des statistiques de visionnage.
    excluded_emby_user_ids: list[Annotated[str, StringConstraints(max_length=64)]] = Field(default=[], max_length=500)


class ConnectionTestRequest(BaseModel):
    url: Optional[str] = None
    api_key: Optional[str] = None
    # Serveur multimédia uniquement : Emby ou Jellyfin.
    media_server: Optional[MediaServer] = None
    username: Optional[str] = None
    password: Optional[str] = None


class NotificationTestResult(BaseModel):
    # Canal -> message d'erreur, ou None si l'envoi a réussi.
    results: dict[str, Optional[str]]


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
