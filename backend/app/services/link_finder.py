"""Où vivent les autres liens d'un fichier ? Quand la suppression d'un média
ne libère pas tout son espace (`disk_footprint.MediaSpace`), c'est qu'un
autre lien vers ses fichiers existe ailleurs : torrent non rattaché, copie
hardlinkée, lien laissé par cross-seed… Le système de fichiers ne sait pas
remonter d'un inode à ses chemins : il faut parcourir l'arborescence.

Lecture seule (parcours de dossiers, `stat`), à la demande seulement, et
borné : uniquement sous les dossiers configurés (leur dossier commun s'il a
un sens, sinon chacun), au plus `MAX_RESULTS` chemins et `BUDGET_SECONDS`
secondes — un disque de plusieurs centaines de milliers de fichiers n'est
pas forcément parcouru en entier, et la réponse le dit."""

import logging
import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.models.settings import Settings

logger = logging.getLogger(__name__)

MAX_RESULTS = 20
BUDGET_SECONDS = 20.0


@dataclass(frozen=True)
class LinkSearch:
    paths: list[str]
    complete: bool
    roots: list[str]


def search_roots(settings: Settings | None) -> list[str]:
    """Dossier commun à la bibliothèque et aux téléchargements (`/data` dans
    l'organisation recommandée : les liens laissés à côté sont trouvés aussi),
    sinon chaque dossier séparément. Jamais la racine du conteneur."""
    if settings is None:
        return []
    roots = [p.rstrip("/\\") for p in (settings.emby_library_path, settings.qbittorrent_download_path) if p]
    roots = [p for p in roots if p and os.path.isdir(p)]
    if len(roots) > 1:
        try:
            common = os.path.commonpath(roots)
        except ValueError:
            common = ""
        if common and os.path.dirname(common) != common:
            return [common]
    return sorted(set(roots))


def _is_link_to(entry: os.DirEntry[str], targets: Mapping[tuple[int, int], int], sizes: set[int]) -> bool:
    """Filtre par taille (gratuit), puis vrai `stat` : l'inode d'une entrée de
    dossier n'est pas fiable partout (nul sous Windows, propre à FUSE sur
    certains montages comme shfs d'Unraid)."""
    if not entry.is_file(follow_symlinks=False) or entry.stat(follow_symlinks=False).st_size not in sizes:
        return False
    st = os.stat(entry.path, follow_symlinks=False)
    return (st.st_ino, st.st_dev) in targets


def find_other_links(
    roots: Iterable[str],
    targets: Mapping[tuple[int, int], int],
    known: set[str],
    *,
    budget: float = BUDGET_SECONDS,
    limit: int = MAX_RESULTS,
) -> LinkSearch:
    """Chemins sous `roots` qui pointent vers l'un de ces fichiers
    ((inode, périphérique) → taille), hors des chemins déjà connus du média.
    Les liens symboliques ne sont pas suivis (ils ne retiennent aucun
    espace)."""
    root_list = list(roots)
    found: list[str] = []
    deadline = time.monotonic() + budget
    pending = list(root_list)
    sizes = set(targets.values())
    while pending and targets:
        if time.monotonic() >= deadline or len(found) >= limit:
            return LinkSearch(paths=found[:limit], complete=False, roots=root_list)
        folder = pending.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            logger.debug("Dossier illisible pendant la recherche de liens : %s", folder, exc_info=True)
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                elif _is_link_to(entry, targets, sizes) and entry.path not in known:
                    found.append(entry.path)
            except OSError:
                logger.debug("Entrée illisible pendant la recherche de liens : %s", entry.path, exc_info=True)
    return LinkSearch(paths=found[:limit], complete=True, roots=root_list)
