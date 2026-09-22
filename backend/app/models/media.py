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
    # Instance Sonarr/Radarr supplémentaire qui suit ce média (ArrInstance.id) ;
    # None = instance principale. Les ids Sonarr/Radarr ne sont uniques qu'au
    # sein d'une instance.
    arr_instance_id: Optional[int] = None
    emby_item_id: Optional[str] = None

    tmdb_id: Optional[int] = None
    tvdb_id: Optional[int] = None
    imdb_id: Optional[str] = None

    has_poster: bool = False
    # Étiquette de version de la jaquette côté Emby (`ImageTags.Primary`) —
    # change quand l'image change. Sert de clé de cache : voir
    # services/poster_cache.py.
    poster_image_tag: Optional[str] = None

    # Dossier racine du média côté Sonarr/Radarr et titres alternatifs : servent
    # au rattachement des torrents (repli par chemin et par similarité de
    # titre). Mémorisés ici pour qu'une analyse partielle rattache exactement
    # comme un scan complet, sans redemander la liste à Sonarr/Radarr.
    root_path: Optional[str] = None
    alt_titles: str = ""

    # Liste de statuts séparés par des virgules parmi doublon/orphelin_qbit/tracker_unique.
    # Vide = sain. Un média peut cumuler plusieurs statuts.
    statuses: str = ""

    # Séries uniquement : épisodes que Sonarr a téléchargés (episodeFile
    # existant) mais qu'Emby n'a PAS repris dans sa bibliothèque, ex "S05E07,
    # S05E08" — un import Emby manqué sur certains épisodes seulement, alors
    # que la série elle-même EST bien présente dans Emby (donc emby_item_id
    # est renseigné). Distinct de manquant_emby "série entière absente" :
    # voir compute_statuses dans scan.py.
    missing_emby_episodes: str = ""

    # Taille totale récupérable estimée (fichiers en doublon + torrents orphelins), en octets.
    reclaimable_bytes: int = 0

    # Taille des fichiers actuellement suivis (hors doublons), en octets —
    # sert au tri "candidats au nettoyage" sans recharger les fichiers.
    total_size: int = 0

    # Date d'ajout dans Emby (`DateCreated`) et, pour une série, nombre
    # d'épisodes présents dans Emby (dénominateur du visionnage "18/20").
    emby_date_added: Optional[datetime] = None
    episode_count: int = 0

    # Agrégats de visionnage (voir services/watch_stats.py), calculés sur les
    # seuls utilisateurs Emby pris en compte : actifs, ayant accès au média,
    # non exclus dans les réglages. Stockés pour filtrer/trier la bibliothèque.
    watch_user_count: int = 0
    watch_played_count: int = 0
    watch_in_progress_count: int = 0
    last_played_at: Optional[datetime] = None

    # Seer : nom du demandeur de la plus ancienne demande rattachée (carte de
    # la bibliothèque). Détail complet dans MediaRequest.
    requested_by: Optional[str] = None

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

    # Identité côté Sonarr/Radarr (jamais Emby) pour la suppression manuelle
    # depuis Analysarr (routers/media.py, delete-selection) : sonarr_episode_id
    # sert au (dé)monitoring (PUT /api/v3/episode/monitor), arr_file_id est
    # l'episodeFile (Sonarr) ou le movieFile (Radarr) à supprimer. None si ce
    # fichier n'a pas pu être rapproché d'une entrée Sonarr/Radarr (ex :
    # fichier Emby en trop, jamais suivi par l'un ou l'autre).
    sonarr_episode_id: Optional[int] = None
    arr_file_id: Optional[int] = None

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
    # Catégorie qBittorrent brute (torrents/info), si définie — sert entre
    # autres à détecter les torrents ajoutés par cross-seed (voir
    # TorrentRead.is_cross_seed dans schemas/media.py).
    category: Optional[str] = None
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


class EmbyUser(SQLModel, table=True):
    """Utilisateur Emby (cache reconstruit à chaque scan et à chaque
    rafraîchissement d'une fiche). `id` : identifiant Emby."""

    id: str = Field(primary_key=True)
    name: str
    # Étiquette de version de l'avatar (`PrimaryImageTag`) : clé de cache,
    # comme pour les jaquettes. None = pas d'avatar.
    image_tag: Optional[str] = None
    is_disabled: bool = False


class MediaWatch(SQLModel, table=True):
    """État de visionnage d'un média par un utilisateur Emby. Une ligne
    n'existe que si l'utilisateur a accès au média dans Emby."""

    id: Optional[int] = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True)
    emby_user_id: str = Field(index=True)

    # Vu entièrement (film marqué vu, ou tous les épisodes d'une série vus).
    played: bool = False
    # Films : pourcentage de lecture (0-100). Séries : nombre d'épisodes vus.
    progress: float = 0
    # Lecture commencée sans être terminée (film entamé, épisode en cours ou
    # série partiellement vue).
    in_progress: bool = False
    last_played_at: Optional[datetime] = None


class TorrentFile(SQLModel, table=True):
    """Fichiers d'un torrent, mémorisés au moment où le client les donne.

    Sert aux analyses par service : après un rafraîchissement de la
    bibliothèque, l'état « hardlinké » et « réparable » de chaque torrent est
    recalculé en relisant les inodes de ces chemins sur le disque, sans
    redemander au client torrent ses fichiers un par un (deux appels par
    torrent, le point le plus lent d'un scan)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    torrent_hash: str = Field(index=True)
    path: str
    size: Optional[int] = None


class ImportIssue(SQLModel, table=True):
    """Entrée problématique de la file d'attente Sonarr/Radarr (cache
    reconstruit à chaque scan, comme le reste) : soit un fichier téléchargé que
    Sonarr/Radarr n'a pas réussi à ranger (`kind = "import"`), soit un
    téléchargement qui n'avance plus (`kind = "stalled"`). Dans les deux cas le
    média n'est pas « absent du serveur multimédia » : il est bloqué en
    amont."""

    id: Optional[int] = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True)

    # "import" (rangement impossible) ou "stalled" (téléchargement en souffrance).
    kind: str = "import"

    # Identifiant de l'entrée dans la file d'attente Sonarr/Radarr.
    queue_id: Optional[int] = None
    # Identifiant du téléchargement côté client torrent (hash pour qBittorrent).
    # Sert à demander à Sonarr/Radarr les fichiers importables de CE
    # téléchargement, jamais fourni par l'utilisateur.
    download_id: Optional[str] = None

    # Dossier de sortie du téléchargement (`outputPath` de la file d'attente) :
    # seul repli quand Sonarr/Radarr ne reconnaît plus le `download_id`.
    output_path: Optional[str] = None

    title: str = ""
    # trackedDownloadState renvoyé par Sonarr/Radarr (importBlocked, importFailed...).
    state: str = ""
    # Motifs concaténés (statusMessages), tronqués : affichés tels quels.
    reason: str = ""
    size: Optional[int] = None
    # Épisodes concernés pour une série ("S01E03"), vide pour un film.
    episode_label: str = ""


class MediaRequest(SQLModel, table=True):
    """Demande Seer rattachée à un média (cache reconstruit à chaque scan).
    Rattachement par identifiant TMDB (films) ou TVDB (séries)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True)

    seer_request_id: int
    # Fiche du média côté Seer : la supprimer retire aussi ses demandes et le
    # rend de nouveau demandable.
    seer_media_id: Optional[int] = None

    # pending | approved | declined | failed | completed
    status: str
    is_4k: bool = False
    # Séries : saisons demandées ("1,2"), vide pour un film.
    seasons: str = ""
    requested_at: Optional[datetime] = None

    requested_by_name: Optional[str] = None
    # `jellyfinUserId` Seer = identifiant Emby (utilisateurs importés d'Emby) :
    # permet d'afficher l'avatar Emby déjà relayé par Analysarr.
    requested_by_emby_id: Optional[str] = None
    # Utilisateur ayant approuvé ou refusé la demande (`modifiedBy`).
    modified_by_name: Optional[str] = None
    modified_by_emby_id: Optional[str] = None
    # Approuvée sans intervention : Seer renseigne alors le demandeur lui-même
    # comme `modifiedBy` (permission d'auto-approbation).
    auto_approved: bool = False


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
    non_hardlink_count: int = 0
    tracker_unique_count: int = 0

    # Taux de rattachement torrent -> média : combien de torrents qBittorrent
    # existent réellement, contre combien ont pu être rattachés à un média
    # connu. Un écart important signale un problème de correspondance (chemins
    # non montés, torrent sans historique Sonarr/Radarr et hors des dossiers
    # connus...) plutôt qu'une vraie absence de contenu.
    qbittorrent_torrent_count: int = 0
    qbittorrent_matched_count: int = 0

    # "manual" (bouton/API) ou "scheduled" (planificateur) — distingue les
    # deux dans l'historique des scans.
    trigger: str = "manual"

    # Périmètre analysé : "full" (tout), ou un service — "radarr", "sonarr",
    # "media_server", "torrents", "queue", "watch", "seer". Voir
    # services/scan_scopes.py.
    scope: str = "full"
