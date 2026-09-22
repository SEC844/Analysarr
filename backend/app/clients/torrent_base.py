"""Socle commun aux clients torrent (qBittorrent, Deluge, Transmission).

Tous exposent la même interface et renvoient la MÊME forme de données que
l'API qBittorrent — `hash`, `name`, `save_path`, `content_path`, `category`,
`size`, `ratio`, `num_seeds`, `num_leechs`, `added_on`, `completion_on` — pour
que le scan, la suppression et les diagnostics ne connaissent qu'un seul
format, quel que soit le client configuré."""

import os
from typing import Any
from urllib.parse import quote


class TorrentAuthError(Exception):
    """Identifiants refusés par le client torrent."""


class TorrentClient:
    """Interface commune. Chaque client s'utilise via `async with`."""

    #: Nom affiché du client (messages de diagnostic).
    name = "client torrent"

    async def __aenter__(self) -> "TorrentClient":
        raise NotImplementedError

    async def __aexit__(self, *exc_info: object) -> None:
        raise NotImplementedError

    async def get_torrents(self) -> list[dict[str, Any]]:
        """Tous les torrents, au format commun décrit en tête de module."""
        raise NotImplementedError

    async def get_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        """[{"url": ..., "status": <int|None>}] — `status` n'existe que côté
        qBittorrent (voir services/trackers.py)."""
        raise NotImplementedError

    async def get_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        """[{"name": <chemin relatif à save_path>, "size": <octets|None>}].
        Indispensable pour les torrents multi-fichiers : `content_path` n'y
        désigne que le dossier racine."""
        raise NotImplementedError

    async def delete_torrents(self, hashes: list[str], delete_files: bool) -> None:
        raise NotImplementedError

    async def export_torrent(self, torrent_hash: str) -> bytes | None:
        """Fichier .torrent du torrent, quand le client sait l'exporter
        (qBittorrent). `None` sinon : la corbeille se rabat alors sur un lien
        magnet construit à partir du hash et des trackers."""
        return None

    async def add_torrent(
        self,
        *,
        torrent: bytes | None = None,
        magnet: str | None = None,
        save_path: str | None = None,
        category: str | None = None,
        paused: bool = False,
    ) -> None:
        """Ajoute un torrent dont les données sont DÉJÀ sur le disque (voir
        services/trash.py) : le client vérifie les fichiers en place et reprend
        le seed, sans rien retélécharger."""
        raise NotImplementedError


def magnet_for(torrent_hash: str, name: str | None, trackers: list[str]) -> str:
    """Lien magnet de repli pour les clients sans export .torrent. Les URL de
    trackers viennent du client lui-même (passkey comprise) : c'est ce qui
    permet de retrouver les métadonnées du torrent à la restauration."""
    magnet = f"magnet:?xt=urn:btih:{torrent_hash}"
    if name:
        magnet += f"&dn={quote(name, safe='')}"
    for url in trackers:
        if url.startswith(("http://", "https://", "udp://")):
            magnet += f"&tr={quote(url, safe='')}"
    return magnet


def content_path_for(save_path: str | None, file_paths: list[str]) -> str | None:
    """Équivalent du `content_path` de qBittorrent pour les clients qui ne le
    fournissent pas : le fichier lui-même pour un torrent mono-fichier, son
    dossier racine pour un torrent multi-fichiers."""
    if not save_path or not file_paths:
        return save_path
    first = file_paths[0].replace("\\", "/").lstrip("/")
    root = first.split("/", 1)[0]
    return os.path.join(save_path, root) if root else save_path
