from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Settings(SQLModel, table=True):
    """Ligne unique (id=1) contenant toute la configuration applicative."""

    id: int | None = Field(default=1, primary_key=True)

    # Serveur multimédia : "emby" ou "jellyfin" (même API à quelques détails
    # près, voir clients/emby.py). Les champs emby_* désignent ce serveur,
    # quel qu'il soit — noms conservés pour ne pas casser les configurations.
    media_server: str = "emby"
    emby_url: str | None = None
    emby_api_key: str | None = None

    sonarr_url: str | None = None
    sonarr_api_key: str | None = None

    radarr_url: str | None = None
    radarr_api_key: str | None = None

    # Client torrent : "qbittorrent" (défaut), "deluge" ou "transmission".
    # Les champs qbittorrent_* ci-dessous servent aux trois (renommer les
    # colonnes casserait les configurations existantes) : Deluge n'utilise que
    # le mot de passe de son interface web, Transmission peut n'avoir aucun
    # identifiant.
    torrent_client: str = "qbittorrent"
    qbittorrent_url: str | None = None
    qbittorrent_username: str | None = None
    qbittorrent_password: str | None = None

    emby_library_path: str | None = None
    qbittorrent_download_path: str | None = None

    cross_seed_enabled: bool = False
    cross_seed_url: str | None = None
    cross_seed_api_key: str | None = None
    # Le dossier de bibliothèque (emby_library_path) tel que vu depuis le
    # CONTENEUR cross-seed, qui peut monter le même volume à un chemin
    # différent (ex: /media côté Analysarr/Emby, /data/media côté cross-seed).
    # Sert à traduire les chemins Emby avant de les envoyer au webhook
    # `path=` — sans quoi cross-seed rejette un chemin qu'il ne peut pas
    # résoudre sur son propre système de fichiers.
    cross_seed_library_path: str | None = None

    # Gestionnaire de demandes : optionnel, jamais requis, en lecture seule.
    # Sert à afficher qui a demandé un média. `seer_type` : "seer" (Overseerr,
    # Jellyseerr, Seerr) ou "ombi" ; les champs `seer_*` servent aux deux.
    seer_enabled: bool = False
    seer_type: str = "seer"
    seer_url: str | None = None
    seer_api_key: str | None = None

    scan_schedule_enabled: bool = False
    scan_schedule_interval_minutes: int | None = None

    # Préférences de l'application (Réglages → Application). `language` None =
    # pas encore choisie : le frontend suit alors la langue du navigateur.
    language: str | None = None
    # Vérifie périodiquement sur GitHub si une nouvelle version est publiée
    # (voir services/updates.py). Désactivable : aucune requête sortante alors.
    update_check_enabled: bool = True
    # Garde-fou des automatisations (services/automation_guard.py) : part de la
    # bibliothèque qui peut basculer d'un scan à l'autre avant la mise en pause.
    automation_guard_percent: int = 20
    # Mise en pause effective : date + motif (JSON) du basculement détecté.
    automations_paused_at: datetime | None = None
    automations_paused_reason: str | None = None
    # Reverse-proxys de confiance (IP ou CIDR, séparés par des virgules) :
    # seule leur requête autorise la lecture de X-Forwarded-For pour connaître
    # l'adresse réelle du client (verrouillage et journal de connexion).
    trusted_proxies: str = ""
    # Corbeille (services/trash.py) : désactivée par défaut — l'espace n'est
    # libéré qu'à la purge, ce qui n'est pas ce qu'on attend d'un outil de
    # nettoyage tant qu'on ne l'a pas demandé.
    trash_enabled: bool = False
    trash_retention_days: int = 7
    # Dernière version annoncée par notification : une version n'est notifiée
    # qu'une fois, même si la vérification périodique repasse toutes les 3 h.
    update_notified_version: str | None = None

    # Identifiants des utilisateurs Emby exclus des statistiques de visionnage
    # (liste JSON) — comptes de test, TV partagée... Les comptes désactivés
    # dans Emby sont exclus d'office, sans figurer ici.
    excluded_emby_user_ids: str = "[]"

    # Préférences d'affichage de l'interface (objet JSON, voir
    # schemas/app.py::UiPreferences — valeurs par défaut appliquées à la
    # lecture, une clé absente n'est donc jamais un problème).
    ui_preferences: str = "{}"

    # Anciens réglages de notification (un seul canal par type), repris
    # automatiquement dans la table NotificationChannel au démarrage puis
    # effacés — conservés ici uniquement pour cette reprise (voir database.py).
    notify_discord_webhook: str | None = None
    notify_ntfy_url: str | None = None
    notify_ntfy_token: str | None = None
    notify_gotify_url: str | None = None
    notify_gotify_token: str | None = None
    notify_on_scan: bool = False
    notify_on_scan_failure: bool = True
    notify_on_actions: bool = True

    # Widget externe (GET /api/status, voir routers/widget.py) : hash sha256 de
    # la clé API, jamais la clé elle-même. None = widget désactivé.
    widget_api_key_hash: str | None = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
