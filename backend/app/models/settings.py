from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Settings(SQLModel, table=True):
    """Ligne unique (id=1) contenant toute la configuration applicative."""

    id: Optional[int] = Field(default=1, primary_key=True)

    # Serveur multimédia : "emby" ou "jellyfin" (même API à quelques détails
    # près, voir clients/emby.py). Les champs emby_* désignent ce serveur,
    # quel qu'il soit — noms conservés pour ne pas casser les configurations.
    media_server: str = "emby"
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

    # Seer (Overseerr/Jellyseerr/Seerr) : optionnel, jamais requis. Sert à
    # afficher qui a demandé un média et à retirer sa demande à la suppression.
    seer_enabled: bool = False
    seer_url: Optional[str] = None
    seer_api_key: Optional[str] = None

    scan_schedule_enabled: bool = False
    scan_schedule_interval_minutes: Optional[int] = None

    # Préférences de l'application (Réglages → Application). `language` None =
    # pas encore choisie : le frontend suit alors la langue du navigateur.
    language: Optional[str] = None
    # Vérifie périodiquement sur GitHub si une nouvelle version est publiée
    # (voir services/updates.py). Désactivable : aucune requête sortante alors.
    update_check_enabled: bool = True

    # Identifiants des utilisateurs Emby exclus des statistiques de visionnage
    # (liste JSON) — comptes de test, TV partagée... Les comptes désactivés
    # dans Emby sont exclus d'office, sans figurer ici.
    excluded_emby_user_ids: str = "[]"

    # Préférences d'affichage de l'interface (objet JSON, voir
    # schemas/app.py::UiPreferences — valeurs par défaut appliquées à la
    # lecture, une clé absente n'est donc jamais un problème).
    ui_preferences: str = "{}"

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
