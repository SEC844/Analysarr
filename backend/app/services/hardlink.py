import os
import re
import stat as stat_module

from app.models.media import MediaFile, MediaType

_EPISODE_PATTERN = re.compile(r"s(\d{1,2})e(\d{1,3})", re.IGNORECASE)


def episode_label_from_filename(name: str) -> str | None:
    """Extrait "S01E02" d'un nom de fichier de release — utilisé pour apparier
    un fichier de torrent à l'épisode Emby correspondant, indépendamment de
    tout autre signal (inode, historique Sonarr/Radarr)."""
    match = _EPISODE_PATTERN.search(name)
    if not match:
        return None
    return f"S{int(match.group(1)):02d}E{int(match.group(2)):02d}"


def stat_inode(path: str | None) -> tuple[int, int] | None:
    """(inode, device) du FICHIER, ou None si le chemin est vide/introuvable/inaccessible/
    n'est pas un fichier régulier.

    Suppose que les chemins renvoyés par Emby/Sonarr/Radarr/qBittorrent sont directement
    utilisables tels quels par le conteneur Analysarr : c'est une précondition déjà requise
    pour que les hardlinks Sonarr/Radarr → qBittorrent fonctionnent (montages unifiés entre
    tous les conteneurs). Aucune traduction de chemin n'est donc tentée ici.

    Rejeter explicitement les dossiers est essentiel : si la résolution d'un chemin de
    fichier torrent se replie par erreur sur son dossier parent (ex: nom de fichier vide
    renvoyé par l'API qBittorrent pour une entrée), comparer l'inode de ce dossier peut
    faussement faire correspondre entre eux tous les torrents qui y sont stockés à plat,
    quel que soit le média réel auquel ils appartiennent.
    """
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not stat_module.S_ISREG(st.st_mode):
        return None
    return st.st_ino, st.st_dev


def resolve_current_files(
    files: list[MediaFile], media_type: MediaType
) -> tuple[dict[str, MediaFile], MediaFile | None]:
    """Fichier(s) de la bibliothèque à comparer/remplacer lors d'une réparation
    de hardlink (ou d'une détection de contenu identique) : celui marqué
    `is_current` par Sonarr/Radarr en priorité, mais à défaut — is_current n'a
    pas pu être déterminé (échec de l'appel Sonarr/Radarr pendant le scan), ou
    le chemin Sonarr/Radarr diverge textuellement du chemin Emby bien
    qu'identique sur le disque — le seul fichier existant pour cet épisode/ce
    média quand il n'y a AUCUNE ambiguïté (un seul fichier, pas de doublon) :
    plus fiable que de ne rien proposer du tout.

    Séries : renvoie {episode_label: fichier} (dict, second élément None).
    Films : renvoie (dict vide, fichier unique ou None).
    """
    if media_type == MediaType.series:
        groups: dict[str, list[MediaFile]] = {}
        for f in files:
            if f.episode_label:
                groups.setdefault(f.episode_label, []).append(f)
        by_episode: dict[str, MediaFile] = {}
        for label, group in groups.items():
            current = next((f for f in group if f.is_current), None)
            if current is None and len(group) == 1:
                current = group[0]
            if current is not None:
                by_episode[label] = current
        return by_episode, None

    current = next((f for f in files if f.is_current), None)
    if current is None and len(files) == 1:
        current = files[0]
    return {}, current
