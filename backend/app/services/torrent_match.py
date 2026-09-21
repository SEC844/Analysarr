"""Rattachement torrent → média, extrait du scan complet pour être réutilisé
tel quel par les analyses partielles (client torrent seul) et par l'analyse
d'un seul média. Une seule implémentation : une analyse partielle ne doit
jamais rattacher différemment du scan complet.

Les règles elles-mêmes sont décrites dans CLAUDE.md (« Correspondance torrent →
média ») : inode d'abord, puis historique Sonarr/Radarr, puis chemin racine,
puis héritage entre copies cross-seed, puis similarité de titre.
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.clients.torrent import TorrentAuthError, torrent_client, torrent_client_name
from app.models.media import MediaFile, MediaType, Torrent
from app.models.settings import Settings
from app.services.hardlink import episode_label_from_filename, resolve_current_files, stat_inode
from app.services.trackers import extract_tracker_domain, status_label

__all__ = [
    "FetchedTorrents",
    "MediaView",
    "attach_torrents",
    "fetch_torrents",
    "is_usable_root",
    "persist_files",
    "torrents_from_cache",
]


def is_usable_root(root_path: str, qbittorrent_download_path: str | None) -> bool:
    """Faux si `root_path` est trop générique pour servir de repli de rattachement
    par chemin — c'est-à-dire s'il est égal à, ou un ancêtre de, la racine des
    téléchargements du client torrent. Un tel chemin correspondrait par préfixe
    à N'IMPORTE QUEL torrent, quel que soit son média réel."""
    if not qbittorrent_download_path:
        return True
    normalized_root = root_path.rstrip("/\\")
    normalized_download = qbittorrent_download_path.rstrip("/\\")
    if normalized_root == normalized_download:
        return False
    return not (
        normalized_download.startswith(normalized_root + "/")
        or normalized_download.startswith(normalized_root + "\\")
    )


def epoch_to_datetime(value: Any) -> datetime | None:
    """Les clients torrent renvoient -1 (voire 0) pour un horodatage non défini."""
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


# Tags de release à ignorer pour le rattachement par similarité de titre
# (passe 3) : qualité, source, codec, audio, langue, groupe. Volontairement
# large plutôt qu'exhaustif — un tag non reconnu qui reste dans les mots ne
# fait qu'empêcher un match plutôt que d'en créer un faux.
_RELEASE_TAG_PATTERN = re.compile(
    r"\b("
    r"\d{3,4}p|4k|8k|"
    r"web[-.]?dl|webrip|web|bluray|blu-ray|bdrip|brrip|hdtv|dvdrip|hdrip|remux|"
    r"x264|x265|h264|h265|hevc|avc|xvid|"
    r"aac\d?|ac3|ac-3|eac3|dts(-?hd)?|ddp?\d(\.\d)?|truehd|flac|mp3|"
    r"multi|vostfr|vfi|vff|vf2|vf|french|truefrench|english|"
    r"integrale|complete|complet|repack|proper|internal|limited|extended|uncut|"
    r"amzn|nf|dsnp|hmax|atvp|itunes|ma"
    r")\b",
    re.IGNORECASE,
)
_SEASON_EPISODE_PATTERN = re.compile(r"\bs\d{1,2}(e\d{1,3})?\b", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b(19\d{2}|20\d{2})\b")


def normalize_words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def normalize_release_words(name: str) -> list[str]:
    """Réduit un nom de release à la liste de mots probablement issus du titre,
    en retirant extension, numérotation saison/épisode et tags
    qualité/codec/langue/groupe — pour un rattachement approximatif par préfixe
    de titre quand ni l'inode ni l'historique Sonarr/Radarr n'ont permis de
    rattacher le torrent."""
    base = os.path.splitext(name)[0]
    base = _SEASON_EPISODE_PATTERN.sub(" ", base)
    base = _RELEASE_TAG_PATTERN.sub(" ", base)
    return normalize_words(base)


@dataclass
class MediaView:
    """Ce dont le rattachement a besoin d'un média, qu'il vienne d'un scan en
    cours ou du cache en base."""

    media_type: MediaType
    title: str
    year: int | None
    alt_titles: list[str]
    root_path: str | None
    # Fichiers de bibliothèque du média (inodes et tailles servent au
    # rattachement et au calcul de `repairable`).
    files: list[MediaFile]


@dataclass
class FetchedTorrents:
    """Torrents du client, déjà résolus (inodes de chaque fichier, trackers)."""

    rows: list[Torrent] = field(default_factory=list)
    content_paths: list[str | None] = field(default_factory=list)
    # Inodes de CHAQUE fichier du torrent, pas un seul par torrent : un pack
    # saison est un torrent multi-fichiers dont `content_path` ne désigne que
    # le dossier racine.
    file_inodes: list[list[tuple[int, int]]] = field(default_factory=list)
    # (nom de fichier, taille annoncée par le client) pour chaque fichier.
    file_details: list[list[tuple[str, int | None]]] = field(default_factory=list)
    # Chemins complets des fichiers, mémorisés en base (table TorrentFile) pour
    # que les analyses par service recalculent les hardlinks sans rappeler le
    # client torrent.
    file_paths: list[list[tuple[str, int | None]]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rows)


async def fetch_torrents(settings: Settings) -> FetchedTorrents:
    """Lit tous les torrents du client configuré, avec leurs trackers et leurs
    fichiers. Une authentification refusée arrête l'analyse : continuer
    donnerait une bibliothèque entièrement « non seedée »."""
    try:
        async with torrent_client(settings) as client:
            torrents = await client.get_torrents()
            trackers_by_hash: dict[str, list[dict[str, Any]]] = {}
            files_by_hash: dict[str, list[dict[str, Any]]] = {}
            for t in torrents:
                try:
                    trackers_by_hash[t["hash"]] = await client.get_trackers(t["hash"])
                except Exception:  # noqa: BLE001 - un tracker illisible ne doit pas arrêter l'analyse
                    trackers_by_hash[t["hash"]] = []
                try:
                    files_by_hash[t["hash"]] = await client.get_files(t["hash"])
                except Exception:  # noqa: BLE001
                    files_by_hash[t["hash"]] = []
    except TorrentAuthError as exc:
        raise RuntimeError(f"Authentification {torrent_client_name(settings)} refusée pendant le scan : {exc}") from exc

    fetched = FetchedTorrents()
    for t in torrents:
        content_path = t.get("content_path") or t.get("save_path")
        save_path = t.get("save_path")

        file_entries: list[tuple[str, int | None]] = []
        for f in files_by_hash.get(t["hash"], []):
            rel = f.get("name")
            if rel and save_path:
                file_entries.append((os.path.join(save_path, rel), f.get("size")))
        if not file_entries and content_path:
            # repli si l'API des fichiers a échoué ou n'a rien renvoyé
            file_entries.append((content_path, t.get("size")))

        resolved_inodes: list[tuple[int, int]] = []
        file_details: list[tuple[str, int | None]] = []
        for path, size in file_entries:
            inode = stat_inode(path)
            if inode is not None:
                resolved_inodes.append(inode)
            file_details.append((os.path.basename(path), size))

        first_inode = resolved_inodes[0] if resolved_inodes else None
        domains = []
        for tr in trackers_by_hash.get(t["hash"], []):
            domain = extract_tracker_domain(tr.get("url", ""))
            if domain:
                domains.append({"domain": domain, "status": status_label(tr.get("status", -1))})

        fetched.rows.append(
            Torrent(
                media_id=0,
                hash=t["hash"],
                name=t.get("name", ""),
                save_path=save_path,
                content_path=t.get("content_path"),
                category=t.get("category") or None,
                size=t.get("size"),
                inode=first_inode[0] if first_inode else None,
                device=first_inode[1] if first_inode else None,
                ratio=t.get("ratio"),
                seeders=t.get("num_seeds"),
                leechers=t.get("num_leechs"),
                added_on=epoch_to_datetime(t.get("added_on")),
                completed_on=epoch_to_datetime(t.get("completion_on")),
                trackers_json=json.dumps(domains),
            )
        )
        fetched.content_paths.append(content_path)
        fetched.file_inodes.append(resolved_inodes)
        fetched.file_details.append(file_details)
        fetched.file_paths.append(file_entries)
    return fetched


def attach_torrents(
    settings: Settings,
    views: list[MediaView],
    fetched: FetchedTorrents,
    hash_to_index: dict[str, int],
) -> list[list[Torrent]]:
    """Rattache chaque torrent à un média (index dans `views`) et renseigne
    `is_hardlinked` / `repairable` / `matched_by_name`. Renvoie les torrents
    par média, dans le même ordre que `views`."""
    # Index des inodes des fichiers de bibliothèque actuels -> média. Signal le
    # plus fiable pour repérer un torrent protégé, quel que soit son chemin de
    # stockage réel — notamment les copies cross-seed.
    library_inode_to_index: dict[tuple[int, int], int] = {}
    for i, view in enumerate(views):
        for f in view.files:
            if f.inode is not None:
                library_inode_to_index[(f.inode, f.device)] = i

    indices: list[int | None] = [None] * len(fetched)
    protected: list[bool] = [False] * len(fetched)
    inode_to_index: dict[tuple[int, int], int] = dict(library_inode_to_index)
    unresolved: list[int] = []

    # Passe 1 : inode de bibliothèque actuel (n'importe lequel des fichiers du
    # torrent), puis historique Sonarr/Radarr, puis chemin racine du média.
    for pos, torrent_row in enumerate(fetched.rows):
        index = None
        for key in fetched.file_inodes[pos]:
            index = library_inode_to_index.get(key)
            if index is not None:
                break
        is_protected = index is not None

        if index is None:
            index = hash_to_index.get(torrent_row.hash.lower())
        if index is None:
            content_path = fetched.content_paths[pos]
            for i, view in enumerate(views):
                if (
                    view.root_path
                    and is_usable_root(view.root_path, settings.qbittorrent_download_path)
                    and content_path
                    and content_path.startswith(view.root_path)
                ):
                    index = i
                    break

        if index is not None:
            indices[pos] = index
            protected[pos] = is_protected
            for key in fetched.file_inodes[pos]:
                inode_to_index.setdefault(key, index)
        else:
            unresolved.append(pos)

    # Passe 2 : copies cross-seed d'un torrent déjà rattaché — aucune trace
    # dans l'historique, mais au moins un fichier (même inode) en commun.
    for pos in unresolved:
        for key in fetched.file_inodes[pos]:
            index = inode_to_index.get(key)
            if index is not None:
                indices[pos] = index
                protected[pos] = False  # sinon la passe 1 l'aurait déjà marqué protégé
                break

    # Passe 3 : repli par similarité de titre, jamais marqué protégé.
    still_unresolved = [pos for pos in unresolved if indices[pos] is None]
    if still_unresolved:
        media_titles: list[tuple[int, list[str]]] = []
        for i, view in enumerate(views):
            if view.title:
                media_titles.append((i, normalize_words(view.title)))
            for alt in view.alt_titles:
                media_titles.append((i, normalize_words(alt)))
        for pos in still_unresolved:
            release_words = normalize_release_words(fetched.rows[pos].name)
            if not release_words:
                continue
            best: tuple[int, int] | None = None  # (longueur du titre, index média)
            for i, title_words in media_titles:
                n = len(title_words)
                if n == 0 or n > len(release_words) or release_words[:n] != title_words:
                    continue
                view = views[i]
                if view.media_type == MediaType.movie and view.year:
                    # Un titre court ("Dune") préfixe aussi bien le film que sa
                    # suite : si UNE année apparaît dans le nom du torrent, elle
                    # doit correspondre. Sans année dans le nom, le titre fait
                    # seul foi.
                    year_in_name = _YEAR_PATTERN.search(fetched.rows[pos].name)
                    if year_in_name and int(year_in_name.group(1)) != view.year:
                        continue
                if best is None or n > best[0]:
                    best = (n, i)
            if best is not None:
                indices[pos] = best[1]
                protected[pos] = False
                fetched.rows[pos].matched_by_name = True

    by_media: list[list[Torrent]] = [[] for _ in views]
    for pos, torrent_row in enumerate(fetched.rows):
        index = indices[pos]
        if index is None:
            continue
        torrent_row.is_hardlinked = None if not fetched.file_inodes[pos] else protected[pos]

        if torrent_row.is_hardlinked is False:
            # Non hardlinké : vrai orphelin, ou simple copie non hardlinkée du
            # fichier actuel ? Seul le contenu fait foi — même épisode/média ET
            # même taille en octets qu'un fichier actuellement suivi.
            view = views[index]
            by_episode, current_single = resolve_current_files(view.files, view.media_type)
            for name, size in fetched.file_details[pos]:
                if size is None:
                    continue
                if view.media_type == MediaType.series:
                    label = episode_label_from_filename(name)
                    current = by_episode.get(label) if label else None
                else:
                    current = current_single
                if current is not None and current.size == size:
                    torrent_row.repairable = True
                    break

        by_media[index].append(torrent_row)
    return by_media

def persist_files(session, fetched: "FetchedTorrents") -> None:
    """Mémorise les fichiers de chaque torrent (table TorrentFile). Remplace
    tout : c'est un cache, reconstruit à chaque lecture du client torrent."""
    from sqlmodel import delete

    from app.models.media import TorrentFile

    session.exec(delete(TorrentFile))
    for row, paths in zip(fetched.rows, fetched.file_paths):
        for path, size in paths:
            session.add(TorrentFile(torrent_hash=row.hash.lower(), path=path, size=size))
    session.commit()


def torrents_from_cache(session) -> "FetchedTorrents":
    """Reconstruit les torrents connus depuis la base, en RELISANT les inodes
    sur le disque. Les chemins viennent du dernier passage sur le client
    torrent ; leur état de hardlink, lui, est réévalué maintenant."""
    from sqlmodel import select

    from app.models.media import Torrent, TorrentFile

    files_by_hash: dict[str, list[tuple[str, int | None]]] = {}
    for row in session.exec(select(TorrentFile)).all():
        files_by_hash.setdefault(row.torrent_hash, []).append((row.path, row.size))

    fetched = FetchedTorrents()
    for torrent in session.exec(select(Torrent)).all():
        entries = files_by_hash.get((torrent.hash or "").lower())
        if not entries:
            content_path = torrent.content_path or torrent.save_path
            entries = [(content_path, torrent.size)] if content_path else []

        resolved_inodes: list[tuple[int, int]] = []
        details: list[tuple[str, int | None]] = []
        for path, size in entries:
            inode = stat_inode(path)
            if inode is not None:
                resolved_inodes.append(inode)
            details.append((os.path.basename(path), size))

        fetched.rows.append(
            Torrent(
                media_id=0,
                hash=torrent.hash,
                name=torrent.name,
                save_path=torrent.save_path,
                content_path=torrent.content_path,
                category=torrent.category,
                size=torrent.size,
                inode=resolved_inodes[0][0] if resolved_inodes else None,
                device=resolved_inodes[0][1] if resolved_inodes else None,
                ratio=torrent.ratio,
                seeders=torrent.seeders,
                leechers=torrent.leechers,
                added_on=torrent.added_on,
                completed_on=torrent.completed_on,
                trackers_json=torrent.trackers_json,
            )
        )
        fetched.content_paths.append(torrent.content_path or torrent.save_path)
        fetched.file_inodes.append(resolved_inodes)
        fetched.file_details.append(details)
        fetched.file_paths.append(entries)
    return fetched
