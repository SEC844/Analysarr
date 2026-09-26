from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ignores import MutedStatusRead


class MediaFileRead(BaseModel):
    id: int
    path: str
    size: int | None
    episode_label: str | None
    is_current: bool
    # Gardé volontairement (voir services/ignores.py) ; `ignorable` : fait
    # partie d'un doublon, peut donc l'être ; `ignore_rule_id` : règle à retirer.
    ignored: bool = False
    ignorable: bool = False
    ignore_rule_id: int | None = None


class TrackerRead(BaseModel):
    domain: str
    status: str


class TorrentRead(BaseModel):
    id: int
    hash: str
    name: str
    save_path: str | None
    content_path: str | None
    size: int | None
    # True si la catégorie qBittorrent ou le chemin de sauvegarde contient
    # "cross-seed" — torrent ajouté par le daemon cross-seed plutôt que
    # grabbé directement par Sonarr/Radarr. Purement indicatif (petit badge
    # sur la fiche média), ne pilote aucune logique de détection.
    is_cross_seed: bool
    is_hardlinked: bool | None
    matched_by_name: bool
    repairable: bool
    # Saisons téléchargées d'avance, pas encore importées (voir Torrent.not_imported).
    not_imported: bool
    # Ignoré (voir services/ignores.py) ; `ignorable` : orphelin ou non
    # hardlinké, peut donc l'être ; `ignore_rule_id` : règle à retirer.
    ignored: bool = False
    ignorable: bool = False
    ignore_rule_id: int | None = None
    ratio: float | None
    seeders: int | None
    leechers: int | None
    added_on: datetime | None
    completed_on: datetime | None
    trackers: list[TrackerRead]


class MediaListItem(BaseModel):
    id: int
    media_type: str
    title: str
    year: int | None
    statuses: list[str]
    # Alertes masquées par l'utilisateur, affichées à part (voir services/ignores.py).
    muted_statuses: list[str] = Field(default_factory=list)
    reclaimable_bytes: int
    total_size: int
    has_poster: bool
    poster_image_tag: str | None
    last_scanned_at: datetime
    has_emby_item: bool
    date_added: datetime | None
    watch_user_count: int
    watch_played_count: int
    watch_in_progress_count: int
    last_played_at: datetime | None
    # Seer activé uniquement : demandeur de la plus ancienne demande.
    requested_by: str | None
    # Instance Sonarr/Radarr supplémentaire qui suit le média (ex : « Radarr
    # 4K ») ; None pour l'instance principale.
    arr_instance_name: str | None = None


class SeerUserRead(BaseModel):
    name: str
    # Compte Emby correspondant (avatar), si retrouvé.
    emby_user_id: str | None
    image_tag: str | None


class MediaRequestRead(BaseModel):
    status: str
    is_4k: bool
    seasons: list[int]
    requested_at: datetime | None
    requested_by: SeerUserRead | None
    # Approbateur (ou auteur du refus) ; None si approuvée automatiquement.
    modified_by: SeerUserRead | None
    auto_approved: bool


class WatchUser(BaseModel):
    id: str
    name: str
    image_tag: str | None
    played: bool
    in_progress: bool
    # Films : pourcentage de lecture (0-100). Séries : nombre d'épisodes vus.
    progress: float
    last_played_at: datetime | None


class MediaWatchStats(BaseModel):
    # False : média absent d'Emby, aucune statistique possible.
    available: bool
    # False : Emby injoignable, chiffres du dernier scan.
    live: bool
    total_episodes: int | None
    users: list[WatchUser]
    played_count: int
    in_progress_count: int
    last_played_at: datetime | None
    last_played_by: str | None
    date_added: datetime | None


class EmbyUserRead(BaseModel):
    id: str
    name: str
    image_tag: str | None
    is_disabled: bool


class ImportIssueRead(BaseModel):
    """Téléchargement que Sonarr/Radarr n'a pas réussi à importer. Aucun chemin
    n'est exposé : seuls le nom de la release et le motif renvoyé par
    Sonarr/Radarr sont affichés."""

    id: int
    # "import" (rangement impossible, relançable) ou "stalled" (téléchargement
    # en souffrance, purement informatif).
    kind: str
    title: str
    state: str
    reason: str
    size: int | None
    episode_label: str
    # Une relance n'est possible que si le téléchargement est encore identifié
    # côté client torrent.
    can_retry: bool


class MediaDetail(MediaListItem):
    radarr_id: int | None
    sonarr_id: int | None
    emby_item_id: str | None
    # Identifiants externes (statut `manquant_arr`) : ce sont eux qui
    # permettent d'ajouter le média dans Radarr ou Sonarr.
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    files: list[MediaFileRead]
    torrents: list[TorrentRead]
    missing_emby_episodes: list[str]
    # Vide si Seer n'est pas activé.
    requests: list["MediaRequestRead"]
    import_issues: list["ImportIssueRead"]
    # Règle de chaque alerte masquée, pour la réafficher.
    muted_rules: list[MutedStatusRead] = Field(default_factory=list)


class ArrCandidateRead(BaseModel):
    """Fiche Sonarr/Radarr proposée pour rattacher un média non suivi."""

    key: str
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    # "certain" : identifiant résolu par Sonarr/Radarr, titre et année
    # concordants. "probable" : trouvé par recherche de titre.
    confidence: str


class ArrLinkPreview(BaseModel):
    service: str
    instance_name: str
    candidates: list[ArrCandidateRead]
    # Dossiers que Sonarr/Radarr voit sur le disque sans média rattaché,
    # exactement la liste de son écran « Import Existing ».
    folders: list[str] = []
    suggested_folder: str | None = None
    quality_profiles: list["ArrQualityProfile"]
    suggested_profile: int | None = None


class ArrQualityProfile(BaseModel):
    id: int
    name: str


class ArrLinkRequest(BaseModel):
    """Choix de l'utilisateur. Le dossier importé n'en fait PAS partie : il est
    recalculé côté serveur à partir des fichiers connus, donc aucun chemin
    arbitraire ne peut être envoyé à Sonarr/Radarr (voir services/arr_link.py)."""

    candidate_key: str = Field(max_length=64)
    quality_profile_id: int = Field(ge=1)
    # Dossier à importer : forcément un de ceux que Sonarr/Radarr a déclarés
    # non rattachés (revérifié côté serveur).
    folder: str | None = Field(default=None, max_length=512)
    # all : tout surveiller · existing : les épisodes présents · future : les
    # prochains · none : ajouter sans surveiller.
    monitor: Literal["all", "existing", "future", "none"] = "none"
    minimum_availability: Literal["announced", "inCinemas", "released"] = "released"


class ArrLinkResult(BaseModel):
    title: str
    service: str


class MediaListResponse(BaseModel):
    items: list[MediaListItem]
    total: int


class ScanRunRead(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    error_message: str | None
    media_count: int
    duplicate_count: int
    orphan_count: int
    tracker_unique_count: int
    qbittorrent_torrent_count: int
    qbittorrent_matched_count: int
    trigger: str
    scope: str = "full"


class DeletePreviewItem(BaseModel):
    kind: str  # "duplicate_file" | "orphan_torrent"
    label: str
    size: int | None


class DeletePreview(BaseModel):
    items: list[DeletePreviewItem]
    total_reclaimable_bytes: int


class DeleteStepResult(BaseModel):
    kind: str
    label: str
    success: bool
    error: str | None = None


class MediaDeleteSelection(BaseModel):
    """Suppression manuelle : contrairement à la suppression cascade
    (doublons/orphelins détectés automatiquement), l'utilisateur choisit
    lui-même quels torrents et/ou quels fichiers de bibliothèque supprimer —
    un seul épisode, une saison entière (plusieurs media_file_ids) ou toute
    la série, jusqu'au film entier."""

    torrent_ids: list[int] = []
    media_file_ids: list[int] = []
    # Arrête aussi le suivi Sonarr/Radarr des fichiers de bibliothèque
    # sélectionnés (pas seulement leur fichier) : empêche un
    # retéléchargement automatique après coup. Radarr : suppression complète
    # du film. Sonarr : démonitoring des épisodes concernés (pas d'équivalent
    # "supprimer" à cette granularité côté Sonarr).
    remove_from_arr: bool = False


class DiskUnit(BaseModel):
    """Un contenu physique sur disque (un inode) : l'espace qu'il occupe
    n'est réellement libéré que si TOUS ses liens (`links`, `st_nlink`) sont
    supprimés — supprimer un torrent hardlinké à la bibliothèque sans
    supprimer aussi le fichier de la bibliothèque ne libère rien."""

    size: int
    links: int


class DeleteFootprintItem(BaseModel):
    id: int
    # Un index dans `MediaDeleteFootprint.units` par fichier physique de
    # l'élément (plusieurs pour un torrent multi-fichiers). Vide pour un
    # lien symbolique : le supprimer ne libère aucun espace.
    units: list[int]


class MediaDeleteFootprint(BaseModel):
    """Empreinte disque des éléments supprimables d'un média, dédupliquée par
    inode, pour calculer côté frontend l'espace RÉELLEMENT libéré par une
    sélection (et non la somme naïve des tailles, fausse dès que des
    fichiers sélectionnés partagent le même inode). Aucun chemin exposé."""

    units: list[DiskUnit]
    torrents: list[DeleteFootprintItem]
    files: list[DeleteFootprintItem]


class MediaDeleteSelectionResult(BaseModel):
    steps: list[DeleteStepResult]
    # True si plus aucun fichier ni torrent ne subsiste pour ce média après
    # la suppression : la fiche média elle-même a été retirée (voir
    # execute_media_delete) — le frontend doit alors quitter la fiche plutôt
    # que d'essayer de la réafficher.
    media_deleted: bool


class DeleteExecuteResult(BaseModel):
    steps: list[DeleteStepResult]


class MediaRescanResultRead(BaseModel):
    """Résultat d'une analyse ciblée sur un seul média."""

    # Vrai si Sonarr/Radarr ne suit plus ce média : sa fiche a été supprimée.
    media_deleted: bool
    files: int
    torrents: int
    import_issues: int
    statuses: list[str]


class ImportRetryResult(BaseModel):
    """Résultat d'une relance d'import, étape par étape (une par
    téléchargement bloqué)."""

    steps: list[DeleteStepResult]
    imported_files: int


class CrossSeedSearchResult(BaseModel):
    triggered: int
    errors: list[str]


class HardlinkRepairItem(BaseModel):
    media_file_id: int
    episode_label: str | None
    torrent_id: int
    torrent_name: str
    # "torrent_to_library" : le fichier de la bibliothèque (non protégé) est
    # remplacé par un hardlink vers le fichier du torrent.
    # "library_to_torrent" : la bibliothèque est déjà protégée par un AUTRE
    # torrent — c'est le fichier de CE torrent qui est remplacé par un
    # hardlink vers le fichier de la bibliothèque, pour qu'il rejoigne le
    # même groupe de hardlinks sans jamais toucher un lien qui fonctionne déjà.
    direction: str
    source_path: str
    target_path: str
    target_exists: bool
    size: int | None


class HardlinkRepairPreview(BaseModel):
    items: list[HardlinkRepairItem]
    # Torrents orphelins pour lesquels aucun fichier de la bibliothèque n'a pu
    # être apparié avec certitude (ex : torrent multi-fichiers ambigu pour un
    # film, fichier introuvable sur disque) — affichés pour transparence, mais
    # non réparables automatiquement.
    unmatched_torrents: list[str]


class HardlinkRepairStepResult(BaseModel):
    media_file_id: int
    label: str
    success: bool
    error: str | None = None
    # True si un hardlink classique était impossible (systèmes de fichiers
    # différents, EXDEV) et qu'un lien SYMBOLIQUE a été utilisé en repli —
    # fonctionnellement équivalent pour l'app (voir _relink), mais suppose
    # que le conteneur qui lit ce chemin (Emby ou qBittorrent) peut aussi
    # résoudre le chemin cible du lien.
    used_symlink: bool = False


class HardlinkRepairResult(BaseModel):
    steps: list[HardlinkRepairStepResult]
    # Taille des copies distinctes remplacées par un lien (espace libéré).
    freed_bytes: int = 0
