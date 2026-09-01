import os


def stat_inode(path: str | None) -> tuple[int, int] | None:
    """(inode, device) du fichier, ou None si le chemin est vide/introuvable/inaccessible.

    Suppose que les chemins renvoyés par Emby/Sonarr/Radarr/qBittorrent sont directement
    utilisables tels quels par le conteneur Analysarr : c'est une précondition déjà requise
    pour que les hardlinks Sonarr/Radarr → qBittorrent fonctionnent (montages unifiés entre
    tous les conteneurs). Aucune traduction de chemin n'est donc tentée ici.
    """
    if not path:
        return None
    try:
        st = os.stat(path)
        return st.st_ino, st.st_dev
    except OSError:
        return None
