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
    has_poster: bool
    last_scanned_at: datetime


class MediaDetail(MediaListItem):
    radarr_id: Optional[int]
    sonarr_id: Optional[int]
    emby_item_id: Optional[str]
    files: list[MediaFileRead]
    torrents: list[TorrentRead]


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
    # Contenu identique, mais torrent et bibliothèque sur des systèmes de
    # fichiers différents : hardlink physiquement impossible (EXDEV), quel
    # que soit le sens du lien — nécessite un changement d'infrastructure.
    cross_filesystem_torrents: list[str]


class HardlinkRepairStepResult(BaseModel):
    media_file_id: int
    label: str
    success: bool
    error: Optional[str] = None


class HardlinkRepairResult(BaseModel):
    steps: list[HardlinkRepairStepResult]
