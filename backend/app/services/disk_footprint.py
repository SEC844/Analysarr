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
from dataclasses import dataclass

from app.schemas.media import DeleteFootprintItem, DiskUnit, MediaDeleteFootprint

logger = logging.getLogger(__name__)

FilePaths = list[tuple[str, int | None]]


class FootprintBuilder:
    """Regroupe des chemins en unités disque, sans jamais compter deux fois
    un même inode."""

    def __init__(self) -> None:
        self.units: list[DiskUnit] = []
        # (inode, périphérique) de chaque unité ; None pour une unité estimée.
        self.keys: list[tuple[int, int] | None] = []
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
            self.keys.append(None)
            return [len(self.units) - 1]
        key = (st.st_ino, st.st_dev)
        if key not in self._by_inode:
            self._by_inode[key] = len(self.units)
            self.units.append(DiskUnit(size=st.st_size, links=st.st_nlink))
            self.keys.append(key)
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


def _selected_links(selected: Iterable[DeleteFootprintItem]) -> dict[int, int]:
    links: dict[int, int] = {}
    for item in selected:
        for unit in item.units:
            links[unit] = links.get(unit, 0) + 1
    return links


def freed_bytes(footprint: MediaDeleteFootprint, selected: Iterable[DeleteFootprintItem]) -> int:
    """Espace libéré en supprimant ces éléments : une unité ne compte que si
    autant de ses liens sont supprimés qu'elle en a (`links`)."""
    selected_links = _selected_links(selected)
    return sum(footprint.units[i].size for i, count in selected_links.items() if count >= footprint.units[i].links)


def whole_media_bytes(footprint: MediaDeleteFootprint) -> int:
    """Espace libéré en supprimant TOUT le média (fichiers et torrents)."""
    return freed_bytes(footprint, [*footprint.files, *footprint.torrents])


CachedFiles = Sequence[tuple[str, int | None]]
CachedTorrents = Sequence[tuple[str | None, str | None, int | None, FilePaths]]


def _cached(files: CachedFiles, torrents: CachedTorrents) -> tuple[FootprintBuilder, MediaDeleteFootprint]:
    builder = FootprintBuilder()
    file_items = [builder.item(index, [(path, size)], size) for index, (path, size) in enumerate(files)]
    torrent_items = [
        builder.item(index, torrent_paths(save_path, content_path, size, paths), size)
        for index, (save_path, content_path, size, paths) in enumerate(torrents)
    ]
    return builder, MediaDeleteFootprint(units=builder.units, files=file_items, torrents=torrent_items)


def cached_footprint(files: CachedFiles, torrents: CachedTorrents) -> MediaDeleteFootprint:
    """Empreinte depuis ce que la base connaît : fichiers de bibliothèque
    `(chemin, taille)` et torrents `(save_path, content_path, taille,
    fichiers mémorisés)`. Identifiants positionnels : seul le total compte."""
    return _cached(files, torrents)[1]


@dataclass(frozen=True)
class MediaSpace:
    """Espace d'un média sur le disque (chaque fichier physique compté une
    fois) et ce que sa suppression complète libérerait réellement. La
    différence est retenue par `external_links` liens qui vivent HORS du
    média (autre torrent, copie hardlinkée, lien laissé par cross-seed…) :
    les supprimer ne libère rien tant que ces liens existent."""

    on_disk: int
    freed: int
    external_links: int
    # Fichiers retenus par un lien extérieur : (inode, périphérique) → taille.
    held_files: dict[tuple[int, int], int]

    @property
    def held(self) -> int:
        return self.on_disk - self.freed


def media_space(files: CachedFiles, torrents: CachedTorrents) -> MediaSpace:
    builder, footprint = _cached(files, torrents)
    selected = _selected_links([*footprint.files, *footprint.torrents])
    held = [i for i, count in selected.items() if count < footprint.units[i].links]
    return MediaSpace(
        on_disk=sum(footprint.units[i].size for i in selected),
        freed=whole_media_bytes(footprint),
        external_links=sum(footprint.units[i].links - selected[i] for i in held),
        held_files={key: footprint.units[i].size for i in held if (key := builder.keys[i]) is not None},
    )
