
from pydantic import BaseModel


class PathCheck(BaseModel):
    label: str
    path: str | None
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
    common_unresolved_prefix: str | None = None


class DiagnosticsResult(BaseModel):
    qbittorrent: PathDiagnostics
    emby: PathDiagnostics
    # Bibliothèque et téléchargements sur deux systèmes de fichiers distincts :
    # aucun hardlink possible entre eux, et la corbeille met les données des
    # torrents de côté dans un `.analysarr-trash` voisin des téléchargements.
    separate_mounts: bool = False


class UnmatchedTorrent(BaseModel):
    hash: str
    name: str
    save_path: str | None
