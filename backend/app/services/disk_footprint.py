"""Empreinte disque d'un média : ses fichiers de bibliothèque et CHAQUE
fichier de chacun de ses torrents, regroupés en unités disque (un inode =
une unité, avec son nombre de liens `st_nlink`).

Une seule façon de compter l'espace libéré par une suppression, partagée
par le dialogue « Supprimer... » (fichiers des torrents demandés en direct au
client, voir media_delete.build_delete_footprint), par le frontend
(lib/footprint.ts, même règle) et par l'espace libérable si l'on supprime
tout le média (`Media.full_reclaimable_bytes`, fichiers des torrents lus dans
le cache `TorrentFile`, aucun appel réseau) : une unité n'est libérée que si
TOUS ses liens font partie de ce qu'on supprime. 100 hardlinks d'un même
fichier = une seule taille, et zéro si un lien vit ailleurs (autre média,
torrent non rattaché, copie hors d'Analysarr).

Lecture seule : `os.stat` et `os.path.islink`."""

import logging
import os
import stat as stat_module
from collections.abc import Iterable, Sequence

from app.schemas.media import DeleteFootprintItem, DiskUnit, MediaDeleteFootprint

logger = logging.getLogger(__name__)

FilePaths = list[tuple[str, int | None]]


class FootprintBuilder:
    """Regroupe des chemins en unités disque, sans jamais compter deux fois
    un même inode."""

    def __init__(self) -> None:
        self.units: list[DiskUnit] = []
        self._by_inode: dict[tuple[int, int], int] = {}

    def unit_for(self, path: str | None, fallback_size: int | None) -> list[int]:
        """Unités d'un chemin. Un lien symbolique n'en a aucune (le supprimer
        ne libère rien) ; un chemin non résolu (dossier, fichier absent)
        devient une unité isolée de la taille connue en base : estimation
        prudente plutôt que de faire disparaître l'élément du calcul."""
        if path and os.path.islink(path):
            return []
        try:
            st = os.stat(path) if path else None
        except OSError:
            logger.debug("Fichier illisible pour l'empreinte disque : %s", path, exc_info=True)
            st = None
        if st is None or not stat_module.S_ISREG(st.st_mode):
            self.units.append(DiskUnit(size=fallback_size or 0, links=1))
            return [len(self.units) - 1]
        key = (st.st_ino, st.st_dev)
        if key not in self._by_inode:
            self._by_inode[key] = len(self.units)
            self.units.append(DiskUnit(size=st.st_size, links=st.st_nlink))
        return [self._by_inode[key]]

    def item(self, item_id: int, paths: FilePaths, fallback_size: int | None) -> DeleteFootprintItem:
        """Élément supprimable (fichier de bibliothèque ou torrent) et ses
        unités ; sans aucun chemin connu, une unité de la taille en base."""
        if not paths:
            return DeleteFootprintItem(id=item_id, units=self.unit_for(None, fallback_size))
        units = [i for path, size in paths for i in self.unit_for(path, size)]
        return DeleteFootprintItem(id=item_id, units=units)


def torrent_paths(save_path: str | None, content_path: str | None, size: int | None, files: FilePaths) -> FilePaths:
    """Fichiers d'un torrent : ceux connus (client ou cache), sinon repli sur
    `content_path` — même règle que hardlink.resolve_torrent_files."""
    if files:
        return files
    return [(content_path, size)] if content_path else []


def freed_bytes(footprint: MediaDeleteFootprint, selected: Iterable[DeleteFootprintItem]) -> int:
    """Espace libéré en supprimant ces éléments : une unité ne compte que si
    autant de ses liens sont supprimés qu'elle en a (`links`)."""
    selected_links: dict[int, int] = {}
    for item in selected:
        for unit in item.units:
            selected_links[unit] = selected_links.get(unit, 0) + 1
    return sum(footprint.units[i].size for i, count in selected_links.items() if count >= footprint.units[i].links)


def whole_media_bytes(footprint: MediaDeleteFootprint) -> int:
    """Espace libéré en supprimant TOUT le média (fichiers et torrents)."""
    return freed_bytes(footprint, [*footprint.files, *footprint.torrents])


def cached_footprint(
    files: Sequence[tuple[str, int | None]],
    torrents: Sequence[tuple[str | None, str | None, int | None, FilePaths]],
) -> MediaDeleteFootprint:
    """Empreinte depuis ce que la base connaît : fichiers de bibliothèque
    `(chemin, taille)` et torrents `(save_path, content_path, taille,
    fichiers mémorisés)`. Identifiants positionnels : seul le total compte."""
    builder = FootprintBuilder()
    file_items = [builder.item(index, [(path, size)], size) for index, (path, size) in enumerate(files)]
    torrent_items = [
        builder.item(index, torrent_paths(save_path, content_path, size, paths), size)
        for index, (save_path, content_path, size, paths) in enumerate(torrents)
    ]
    return MediaDeleteFootprint(units=builder.units, files=file_items, torrents=torrent_items)
