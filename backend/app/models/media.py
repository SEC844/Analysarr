from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MediaType(str, Enum):
    movie = "movie"
    series = "series"


class Media(SQLModel, table=True):
    """Une entrée Sonarr/Radarr, éventuellement liée à un item Emby.
    Reconstruite entièrement à chaque scan (pas de diff incrémental en V1)."""

    id: Optional[int] = Field(default=None, primary_key=True)

    media_type: MediaType
    title: str
    year: Optional[int] = None

    radarr_id: Optional[int] = None
    sonarr_id: Optional[int] = None
    emby_item_id: Optional[str] = None

    tmdb_id: Optional[int] = None
    tvdb_id: Optional[int] = None
    imdb_id: Optional[str] = None

    has_poster: bool = False

    # Liste de statuts séparés par des virgules parmi doublon/orphelin_qbit/tracker_unique.
    # Vide = sain. Un média peut cumuler plusieurs statuts.
    statuses: str = ""

    # Taille totale récupérable estimée (fichiers en doublon + torrents orphelins), en octets.
    reclaimable_bytes: int = 0

    last_scanned_at: datetime = Field(default_factory=_utcnow)


class MediaFile(SQLModel, table=True):
    """Fichier physique côté Emby, associé à un Média. Un média avec plusieurs
    MediaFile pointant vers des inodes différents pour le même épisode/film = doublon."""

    id: Optional[int] = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True)

    path: str
    size: Optional[int] = None
    inode: Optional[int] = None
    device: Optional[int] = None

    # Pour les séries : identifie l'épisode (ex: "S01E02") afin de regrouper les
    # fichiers par épisode plutôt que par série entière. None pour les films.
    episode_label: Optional[str] = None

    # True si ce chemin est celui actuellement suivi par Sonarr/Radarr (movieFile /
    # episodeFile). Les autres fichiers du même groupe sont des doublons "libres",
    # non gérés par Sonarr/Radarr, candidats à la suppression directe.
    is_current: bool = False


class Torrent(SQLModel, table=True):
    """Torrent qBittorrent, rattaché à un Média si l'historique Sonarr/Radarr
    (downloadId) ou, à défaut, une correspondance de chemin l'ont permis."""

    id: Optional[int] = Field(default=None, primary_key=True)
    media_id: Optional[int] = Field(default=None, foreign_key="media.id", index=True)

    hash: str = Field(index=True)
    name: str
    save_path: Optional[str] = None
    content_path: Optional[str] = None
    size: Optional[int] = None

    inode: Optional[int] = None
    device: Optional[int] = None

    # True = un fichier Emby actuel du média partage le même inode (protégé).
    # False = aucun hardlink valide trouvé (orphelin_qbit).
    # None = non évalué (chemins non configurés, ou fichier introuvable).
    is_hardlinked: Optional[bool] = None

    # True si ce torrent n'a été rattaché à son média que par similarité de
    # titre (ni inode, ni historique Sonarr/Radarr, ni chemin) — typiquement un
    # ajout manuel antérieur à la mise en place du hardlink sur le serveur.
    # Rattachement heuristique : is_hardlinked reste False pour ces torrents.
    # (Purement informatif — voir `repairable` pour la classification réelle
    # non hardlink vs orphelin, qui ne se fie pas à cette provenance.)
    matched_by_name: bool = False

    # True si is_hardlinked=False MAIS qu'un fichier de ce torrent a le MÊME
    # contenu qu'un fichier actuellement suivi par la bibliothèque (même
    # épisode/même média, taille en octets identique) : c'est une copie non
    # hardlinkée du fichier actuel, pas une ancienne version — réparable
    # automatiquement. Ne dépend PAS de la façon dont le torrent a été
    # rattaché (historique Sonarr/Radarr ou similarité de titre) : seul le
    # contenu fait foi. False = torrent sans lien de contenu avec un fichier
    # actuel (vrai orphelin, ex: ancienne qualité remplacée par un upgrade).
    repairable: bool = False

    # Données qBittorrent affichées sur la fiche détail (ratio, popularité, ancienneté).
    ratio: Optional[float] = None
    seeders: Optional[int] = None
    leechers: Optional[int] = None
    added_on: Optional[datetime] = None
    completed_on: Optional[datetime] = None

    # JSON list [{"domain": str, "status": str}]
    trackers_json: str = "[]"


class ScanStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"


class ScanRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = None
    status: ScanStatus = ScanStatus.running
    error_message: Optional[str] = None

    media_count: int = 0
    duplicate_count: int = 0
    orphan_count: int = 0
    tracker_unique_count: int = 0

    # Taux de rattachement torrent -> média : combien de torrents qBittorrent
    # existent réellement, contre combien ont pu être rattachés à un média
    # connu. Un écart important signale un problème de correspondance (chemins
    # non montés, torrent sans historique Sonarr/Radarr et hors des dossiers
    # connus...) plutôt qu'une vraie absence de contenu.
    qbittorrent_torrent_count: int = 0
    qbittorrent_matched_count: int = 0
