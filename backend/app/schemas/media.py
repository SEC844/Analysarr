from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class MediaFileRead(BaseModel):
    id: int
    path: str
    size: Optional[int]
    episode_label: Optional[str]
    is_current: bool


class TrackerRead(BaseModel):
    domain: str
    status: str


class TorrentRead(BaseModel):
    id: int
    hash: str
    name: str
    save_path: Optional[str]
    content_path: Optional[str]
    size: Optional[int]
    # True si la catégorie qBittorrent ou le chemin de sauvegarde contient
    # "cross-seed" — torrent ajouté par le daemon cross-seed plutôt que
    # grabbé directement par Sonarr/Radarr. Purement indicatif (petit badge
    # sur la fiche média), ne pilote aucune logique de détection.
    is_cross_seed: bool
    is_hardlinked: Optional[bool]
    matched_by_name: bool
    repairable: bool
    ratio: Optional[float]
    seeders: Optional[int]
    leechers: Optional[int]
    added_on: Optional[datetime]
    completed_on: Optional[datetime]
    trackers: list[TrackerRead]


class MediaListItem(BaseModel):
    id: int
    media_type: str
    title: str
    year: Optional[int]
    statuses: list[str]
    reclaimable_bytes: int
    total_size: int
    has_poster: bool
    poster_image_tag: Optional[str]
    last_scanned_at: datetime
    has_emby_item: bool
    date_added: Optional[datetime]
    watch_user_count: int
    watch_played_count: int
    watch_in_progress_count: int
    last_played_at: Optional[datetime]
    # Seer activé uniquement : demandeur de la plus ancienne demande.
    requested_by: Optional[str]


class SeerUserRead(BaseModel):
    name: str
    # Compte Emby correspondant (avatar), si retrouvé.
    emby_user_id: Optional[str]
    image_tag: Optional[str]


class MediaRequestRead(BaseModel):
    status: str
    is_4k: bool
    seasons: list[int]
    requested_at: Optional[datetime]
    requested_by: Optional[SeerUserRead]
    # Approbateur (ou auteur du refus) ; None si approuvée automatiquement.
    modified_by: Optional[SeerUserRead]
    auto_approved: bool


class WatchUser(BaseModel):
    id: str
    name: str
    image_tag: Optional[str]
    played: bool
    in_progress: bool
    # Films : pourcentage de lecture (0-100). Séries : nombre d'épisodes vus.
    progress: float
    last_played_at: Optional[datetime]


class MediaWatchStats(BaseModel):
    # False : média absent d'Emby, aucune statistique possible.
    available: bool
    # False : Emby injoignable, chiffres du dernier scan.
    live: bool
    total_episodes: Optional[int]
    users: list[WatchUser]
    played_count: int
    in_progress_count: int
    last_played_at: Optional[datetime]
    last_played_by: Optional[str]
    date_added: Optional[datetime]


class EmbyUserRead(BaseModel):
    id: str
    name: str
    image_tag: Optional[str]
    is_disabled: bool


class MediaDetail(MediaListItem):
    radarr_id: Optional[int]
    sonarr_id: Optional[int]
    emby_item_id: Optional[str]
    files: list[MediaFileRead]
    torrents: list[TorrentRead]
    missing_emby_episodes: list[str]
    # Vide si Seer n'est pas activé.
    requests: list["MediaRequestRead"]


class MediaListResponse(BaseModel):
    items: list[MediaListItem]
    total: int


class ScanRunRead(BaseModel):
    id: int
    started_at: datetime
    finished_at: Optional[datetime]
    status: str
    error_message: Optional[str]
    media_count: int
    duplicate_count: int
    orphan_count: int
    tracker_unique_count: int
    qbittorrent_torrent_count: int
    qbittorrent_matched_count: int
    trigger: str


class DeletePreviewItem(BaseModel):
    kind: str  # "duplicate_file" | "orphan_torrent"
    label: str
    size: Optional[int]


class DeletePreview(BaseModel):
    items: list[DeletePreviewItem]
    total_reclaimable_bytes: int


class DeleteStepResult(BaseModel):
    kind: str
    label: str
    success: bool
    error: Optional[str] = None


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
    # Supprime aussi la demande (et la fiche) du média dans Seer. Appliqué
    # seulement si toute la bibliothèque du média est supprimée sans erreur.
    remove_from_seer: bool = False


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


class CrossSeedSearchResult(BaseModel):
    triggered: int
    errors: list[str]


class HardlinkRepairItem(BaseModel):
    media_file_id: int
    episode_label: Optional[str]
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
    size: Optional[int]


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
    error: Optional[str] = None
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
