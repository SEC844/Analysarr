from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Settings(SQLModel, table=True):
    """Ligne unique (id=1) contenant toute la configuration applicative."""

    id: Optional[int] = Field(default=1, primary_key=True)

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
    # Le dossier de bibliothèque (emby_library_path) tel que vu depuis le
    # CONTENEUR cross-seed, qui peut monter le même volume à un chemin
    # différent (ex: /media côté Analysarr/Emby, /data/media côté cross-seed).
    # Sert à traduire les chemins Emby avant de les envoyer au webhook
    # `path=` — sans quoi cross-seed rejette un chemin qu'il ne peut pas
    # résoudre sur son propre système de fichiers.
    cross_seed_library_path: Optional[str] = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
