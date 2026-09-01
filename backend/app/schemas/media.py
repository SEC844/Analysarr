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
