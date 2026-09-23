"""Fichiers de la bibliothèque : identifiants du serveur multimédia, épisodes, et rapprochement
avec les fichiers suivis par Sonarr/Radarr (chemin, inode, nom et taille)."""

import os
from collections.abc import Iterable
from typing import Any

from app.clients.emby import EmbyClient
from app.models.media import (
    MediaFile,
)
from app.services.hardlink import stat_inode


def _provider_id(provider_ids: dict[str, Any] | None, *keys: str) -> str | None:
    lower = {k.lower(): v for k, v in (provider_ids or {}).items()}
    for key in keys:
        value = lower.get(key.lower())
        if value:
            return str(value)
    return None


def _episode_label(item: dict[str, Any]) -> str:
    season = item.get("ParentIndexNumber")
    episode = item.get("IndexNumber")
    if season is None or episode is None:
        return f"item:{item.get('Id')}"
    return f"S{int(season):02d}E{int(episode):02d}"


def _episode_span_labels(item: dict[str, Any]) -> set[str]:
    """Tous les épisodes couverts par un item Emby. Un fichier multi-épisodes
    (S03E01-E02 fusionnés) ne produit qu'UN item, numéroté sur son premier
    épisode et portant `IndexNumberEnd` — sans lui, les épisodes suivants
    passaient pour absents du serveur multimédia (bug réel)."""
    label = _episode_label(item)
    season = item.get("ParentIndexNumber")
    first = item.get("IndexNumber")
    last = item.get("IndexNumberEnd")
    if season is None or first is None or last is None:
        return {label}
    first, last = int(first), int(last)
    # Garde-fou : une borne aberrante ne doit pas masquer des épisodes
    # réellement absents.
    if last <= first or last - first > 50:
        return {label}
    return {f"S{int(season):02d}E{n:02d}" for n in range(first, last + 1)}


def _missing_emby_labels(
    downloaded: set[str], file_labels: Iterable[str | None], spans: dict[str, set[str]]
) -> list[str]:
    """Épisodes téléchargés par Sonarr qu'aucun fichier du serveur multimédia
    ne couvre. `spans` étend chaque fichier aux épisodes qu'il contient."""
    covered: set[str] = set()
    for label in file_labels:
        if label:
            covered |= spans.get(label, {label})
    return sorted(downloaded - covered)


def _media_sources(item: dict[str, Any]) -> list[dict[str, Any]]:
    sources = item.get("MediaSources") or []
    if sources:
        return sources
    path = item.get("Path")
    return [{"Path": path, "Size": None}] if path else []


def _path_parts(path: str) -> list[str]:
    return [part for part in path.replace("\\", "/").split("/") if part]


def _file_match_strength(path: str | None, size: int | None, arr_file: dict[str, Any] | None) -> int:
    """Le fichier du serveur multimédia (`path`, `size`) est-il le fichier que
    Sonarr/Radarr suit (`arr_file` : movieFile/episodeFile) ?

    2 : certain — chemin identique, ou même inode (les deux chemins résolus
        depuis le conteneur Analysarr).
    1 : très probable — conteneurs montant la bibliothèque à des chemins
        différents (ex : /data/media/movies côté Emby, /movies côté Radarr) :
        même nom de fichier ET même taille, ou même dossier parent si une
        taille manque.
    0 : pas le même fichier."""
    if not path or not arr_file:
        return 0
    arr_path = arr_file.get("path") or ""
    if arr_path and path == arr_path:
        return 2
    arr_size = arr_file.get("size")
    sizes_known = size is not None and arr_size is not None
    if sizes_known and size != arr_size:
        return 0
    same_inode = _same_inode(path, arr_path)
    if same_inode is not None:
        return 2 if same_inode else 0
    return _name_match_strength(path, arr_path or arr_file.get("relativePath") or "", sizes_known)


def _same_inode(path: str, arr_path: str) -> bool | None:
    """Même fichier sur le disque ? None si l'un des deux chemins n'est pas
    lisible depuis le conteneur Analysarr."""
    local_inode = stat_inode(path)
    arr_inode = stat_inode(arr_path) if arr_path else None
    if local_inode is None or arr_inode is None:
        return None
    return local_inode == arr_inode


def _name_match_strength(path: str, arr_path: str, sizes_known: bool) -> int:
    """Rapprochement par le nom, pour des conteneurs qui montent la
    bibliothèque à des chemins différents : même nom de fichier et même taille,
    ou même dossier parent si une taille manque."""
    local_parts = _path_parts(path)
    arr_parts = _path_parts(arr_path)
    if not local_parts or not arr_parts or local_parts[-1] != arr_parts[-1]:
        return 0
    if sizes_known:
        return 1
    return 1 if len(local_parts) > 1 and len(arr_parts) > 1 and local_parts[-2] == arr_parts[-2] else 0


def _current_flags(candidates: list[tuple[str | None, int | None]], arr_file: dict[str, Any] | None) -> list[bool]:
    """Pour chaque fichier candidat (même film ou même épisode), True s'il est
    le fichier suivi par Sonarr/Radarr. Un rapprochement seulement probable
    (force 1) n'est retenu que s'il désigne un seul candidat : jamais deux
    fichiers « actuels » pour un même épisode."""
    strengths = [_file_match_strength(path, size, arr_file) for path, size in candidates]
    best = max(strengths, default=0)
    if best == 0 or (best == 1 and strengths.count(1) > 1):
        return [False] * len(candidates)
    return [strength == best for strength in strengths]


def _without_other_instance_files(
    sources: list[dict[str, Any]], own_file: dict[str, Any] | None, other_files: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Retire les fichiers suivis par une AUTRE instance Sonarr/Radarr (ex : la
    version 4K d'un film réunie dans le même item du serveur multimédia) : ce
    ne sont pas des doublons de cette instance, et les proposer au nettoyage
    supprimerait la version de l'autre instance."""
    if not other_files:
        return sources
    kept = []
    for source in sources:
        path, size = source.get("Path"), source.get("Size")
        other = max(_file_match_strength(path, size, f) for f in other_files)
        if other and other > _file_match_strength(path, size, own_file):
            continue
        kept.append(source)
    return kept


def _index_items(items: list[dict[str, Any]], provider: str) -> dict[str, list[dict[str, Any]]]:
    """Items du serveur multimédia par identifiant externe. Plusieurs items
    peuvent partager un identifiant (bibliothèques séparées, ex : 1080p et 4K)."""
    index: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = _provider_id(item.get("ProviderIds"), provider)
        if key:
            index.setdefault(key, []).append(item)
    return index


def _pick_emby_item(candidates: list[dict[str, Any]], arr_files: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Item retenu pour un film : celui qui contient un fichier suivi par cette
    instance quand plusieurs items partagent l'identifiant, sinon le dernier
    (comportement historique)."""
    if len(candidates) > 1:
        for item in candidates:
            sources = _media_sources(item)
            if any(_file_match_strength(s.get("Path"), s.get("Size"), f) for s in sources for f in arr_files):
                return item
    return candidates[-1] if candidates else None


async def _pick_series_item(
    emby: EmbyClient, candidates: list[dict[str, Any]], episode_files: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Même principe que `_pick_emby_item` pour une série, sur ses épisodes.
    Renvoie l'item et ses épisodes (déjà récupérés)."""
    if len(candidates) > 1:
        for item in candidates:
            episodes = await emby.get_episodes(item["Id"])
            sources = [s for episode in episodes for s in _media_sources(episode)]
            if any(_file_match_strength(s.get("Path"), s.get("Size"), f) for s in sources for f in episode_files):
                return item, episodes
    item = candidates[-1]
    return item, await emby.get_episodes(item["Id"])


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _common_root(paths: list[str]) -> str | None:
    """Dossier commun aux fichiers : sert au rattachement des torrents, qui
    compare aussi les chemins (voir services/torrent_match.py)."""
    usable = [os.path.dirname(path) for path in paths if path]
    if not usable:
        return None
    if len(usable) == 1:
        return usable[0]
    try:
        return os.path.commonpath(usable)
    except ValueError:
        return usable[0]


def _library_file(
    source: dict[str, Any],
    label: str | None,
    *,
    is_current: bool = True,
    arr_file_id: int | None = None,
    sonarr_episode_id: int | None = None,
) -> MediaFile:
    """Fichier de la bibliothèque, avec son inode lu sur le disque. Seul point
    du scan qui lit les inodes de la bibliothèque.

    Valeurs par défaut : fichier d'un média qu'aucun Sonarr/Radarr ne suit — il
    n'a pas d'identité arr, et il est par définition le fichier « actuel »."""
    path = source.get("Path")
    inode = stat_inode(path)
    return MediaFile(
        media_id=0,
        path=path or "",
        size=source.get("Size"),
        inode=inode[0] if inode else None,
        device=inode[1] if inode else None,
        episode_label=label,
        is_current=is_current,
        sonarr_episode_id=sonarr_episode_id,
        arr_file_id=arr_file_id,
    )
