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
    # Préfixe de dossier commun à TOUS les chemins non résolus (pas
    # seulement l'échantillon ci-dessus) quand il y en a un — un point de
    # montage manquant (un disque/partage jamais ajouté au conteneur
    # Analysarr) produit typiquement des dizaines de chemins qui partagent
    # tous le même dossier racine inaccessible, plutôt qu'un préfixe global
    # comme "/data" qui serait vrai pour l'ensemble des chemins résolus ET
    # non résolus (peu informatif). None si aucun préfixe commun distinctif.
    common_unresolved_prefix: Optional[str] = None


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


class PathStat(BaseModel):
    path: str
    exists: bool
    is_regular_file: bool
    inode: Optional[int]
    device: Optional[int]


class EmbyFileDebug(BaseModel):
    item_name: str
    episode_label: Optional[str]
    stat: PathStat


class UnmatchedTorrent(BaseModel):
    hash: str
    name: str
    save_path: Optional[str]
