import os
import stat as stat_module


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
