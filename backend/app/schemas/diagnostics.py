from typing import Optional

from pydantic import BaseModel


class PathCheck(BaseModel):
    label: str
    path: Optional[str]
    resolved: bool


class PathDiagnostics(BaseModel):
    total: int
    resolved: int
    unresolved_samples: list[PathCheck]


class DiagnosticsResult(BaseModel):
    qbittorrent: PathDiagnostics
    emby: PathDiagnostics


class TorrentFileDebug(BaseModel):
    relative_name: Optional[str]
    resolved_path: str
    exists: bool
    is_regular_file: bool
    inode: Optional[int]
    device: Optional[int]


class TorrentDebug(BaseModel):
    hash: str
    name: str
    save_path: Optional[str]
    content_path: Optional[str]
    files_api_count: int
    files_api_error: Optional[str]
    files: list[TorrentFileDebug]
