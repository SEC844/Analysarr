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
