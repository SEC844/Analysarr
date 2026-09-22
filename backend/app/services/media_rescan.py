"""Analyse d'un seul média, depuis sa fiche.

Même résultat qu'un scan complet pour CE média — nouveaux torrents détectés,
fichiers disparus retirés, statuts recalculés — mais sans relire toute la
bibliothèque, et sans changer l'identifiant de la fiche (la page reste
valide après l'analyse).

Garde-fous :

- un torrent déjà rattaché à un AUTRE média n'est jamais volé ; seuls sont
  réexaminés les torrents de ce média, ceux qui ne sont rattachés à rien, et
  ceux que le chemin racine ou le nom désignent (voir `_candidate_positions`) ;
- le détail (fichiers, trackers) n'est demandé au client torrent que pour ces
  candidats : c'est ce qui rend l'analyse d'un média rapide ;
- si Sonarr/Radarr ne suit plus le média, sa fiche est supprimée plutôt que
  laissée avec des données mortes ;
- les demandes Seer ne sont pas relues (l'API ne permet pas de filtrer par
  média) : elles restent celles du dernier scan complet.
"""

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from sqlmodel import Session, delete, select

from app.clients.emby import EmbyClient, media_server_client
from app.clients.torrent import torrent_client, torrent_client_configured
from app.models.media import ImportIssue, Media, MediaFile, MediaType, MediaRequest, MediaWatch, Torrent
from app.models.settings import Settings
from app.services.arr_instances import arr_target_for, arr_targets
from app.services.hardlink import stat_inode
from app.services.queue_issues import index_queue_issues, issue_rows_for
from app.services.scan import (
    LibraryContext,
    _current_flags,
    _episode_label,
    _episode_span_labels,
    _media_sources,
    _missing_emby_labels,
    _pick_emby_item,
    _pick_series_item,
    _without_other_instance_files,
    compute_statuses,
    current_files_size,
)
from app.services.trackers import extract_tracker_domain, status_label
from app.services.torrent_match import (
    FetchedTorrents,
    MediaView,
    attach_torrents,
    epoch_to_datetime,
    is_usable_root,
    normalize_release_words,
    normalize_words,
)
from app.services.watch_stats import parse_emby_date, refresh_media_watch

__all__ = ["MediaRescanResult", "rescan_media"]


@dataclass
class MediaRescanResult:
    media_deleted: bool = False
    files: int = 0
    torrents: int = 0
    import_issues: int = 0
    statuses: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.statuses is None:
            self.statuses = []


async def rescan_media(session: Session, settings: Settings, media: Media) -> MediaRescanResult:
    is_series = media.media_type == MediaType.series
    arr_id = media.sonarr_id if is_series else media.radarr_id
    if arr_id is None:
        # Média trouvé dans la bibliothèque seule : rien à demander à
        # Sonarr/Radarr, tout vient du serveur multimédia.
        return await _rescan_untracked(session, settings, media, is_series)

    target = arr_target_for(session, settings, media)
    if target is None:
        raise RuntimeError("Instance Sonarr/Radarr introuvable pour ce média.")

    entry = await (target.sonarr().get_series_by_id(arr_id) if is_series else target.radarr().get_movie(arr_id))
    if entry is None:
        # Plus suivi par Sonarr/Radarr : la fiche n'a plus lieu d'être.
        _delete_media(session, media)
        return MediaRescanResult(media_deleted=True)

    _apply_arr_entry(media, entry, is_series)
    issues = await _rebuild_import_issues(session, settings, media, target, arr_id, is_series)
    files = await _rebuild_files(session, settings, media, target, entry, is_series)
    torrents = await _rebuild_torrents(session, settings, media, files)

    statuses, reclaimable = compute_statuses(
        files,
        torrents,
        bool(media.emby_item_id),
        len([label for label in media.missing_emby_episodes.split(",") if label]),
        {i.download_id.lower() for i in issues if i.download_id},
        {i.kind for i in issues},
    )
    media.statuses = ",".join(sorted(statuses))
    media.reclaimable_bytes = reclaimable
    media.total_size = current_files_size(files)
    session.add(media)
    session.commit()

    # Visionnage : rafraîchi en direct pour ce média seulement (appels
    # restreints). Un échec laisse les chiffres du dernier scan.
    await refresh_media_watch(session, media, settings)
    session.commit()

    return MediaRescanResult(
        files=len(files),
        torrents=len(torrents),
        import_issues=len(issues),
        statuses=sorted(statuses),
    )


async def _adopt_arr_entry(session: Session, settings: Settings, media: Media, is_series: bool) -> bool:
    """Un Sonarr/Radarr suit-il désormais ce média ? Après un rattachement, la
    fiche doit reprendre son identité sans attendre un scan complet : sans
    cette recherche, « Analyser ce média » laissait le statut « Non suivi »
    (bug réel)."""
    for target in arr_targets(session, settings, "sonarr" if is_series else "radarr"):
        try:
            entry = (
                await target.sonarr().find_series(media.tvdb_id)
                if is_series
                else await target.radarr().find_movie(media.tmdb_id, media.imdb_id)
            )
        except httpx.HTTPError:
            continue
        if entry is None:
            continue
        media.arr_instance_id = target.instance_id
        if is_series:
            media.sonarr_id = entry["id"]
        else:
            media.radarr_id = entry["id"]
        session.add(media)
        session.commit()
        return True
    return False


async def _rescan_untracked(
    session: Session, settings: Settings, media: Media, is_series: bool
) -> MediaRescanResult:
    """Relit un média que Sonarr/Radarr ne suit pas : son item du serveur
    multimédia, ses fichiers, puis ses torrents. Si l'item a disparu de la
    bibliothèque, la fiche n'a plus lieu d'être."""
    from app.services.scan import build_untracked_movie, build_untracked_series

    # Un rattachement a pu avoir lieu entre-temps : dans ce cas le média
    # redevient un média suivi, et c'est le chemin normal qui s'applique.
    if await _adopt_arr_entry(session, settings, media, is_series):
        return await rescan_media(session, settings, media)

    emby = media_server_client(settings)
    items = await emby.get_items_by_ids([media.emby_item_id]) if media.emby_item_id else []
    if not items:
        _delete_media(session, media)
        return MediaRescanResult(media_deleted=True)

    item = items[0]
    ctx = LibraryContext(emby=emby)
    built = await build_untracked_series(ctx, item) if is_series else build_untracked_movie(item)
    if built is None:
        _delete_media(session, media)
        return MediaRescanResult(media_deleted=True)

    _apply_media_server_item(media, item)
    media.title = built.media.title
    media.year = built.media.year
    media.episode_count = built.media.episode_count
    media.root_path = built.root_path
    media.tmdb_id, media.tvdb_id, media.imdb_id = built.media.tmdb_id, built.media.tvdb_id, built.media.imdb_id

    session.exec(delete(MediaFile).where(MediaFile.media_id == media.id))
    files: list[MediaFile] = []
    for row in built.files:
        row.media_id = media.id
        session.add(row)
        files.append(row)
    session.commit()

    torrents = await _rebuild_torrents(session, settings, media, files)
    statuses, reclaimable = compute_statuses(files, torrents, bool(media.emby_item_id), tracked_by_arr=False)
    media.statuses = ",".join(sorted(statuses))
    media.reclaimable_bytes = reclaimable
    media.total_size = current_files_size(files)
    session.add(media)
    session.commit()

    await refresh_media_watch(session, media, settings)
    session.commit()
    return MediaRescanResult(files=len(files), torrents=len(torrents), statuses=sorted(statuses))


def _delete_media(session: Session, media: Media) -> None:
    session.exec(delete(ImportIssue).where(ImportIssue.media_id == media.id))
    session.exec(delete(MediaRequest).where(MediaRequest.media_id == media.id))
    session.exec(delete(MediaWatch).where(MediaWatch.media_id == media.id))
    session.exec(delete(Torrent).where(Torrent.media_id == media.id))
    session.exec(delete(MediaFile).where(MediaFile.media_id == media.id))
    session.delete(media)
    session.commit()


def _apply_arr_entry(media: Media, entry: dict[str, Any], is_series: bool) -> None:
    """Remet à jour ce que Sonarr/Radarr porte : titre, année, chemin racine et
    titres alternatifs (ces deux derniers servent au rattachement)."""
    media.title = entry.get("title") or media.title
    media.year = entry.get("year") or media.year
    media.root_path = entry.get("path") or media.root_path
    alt_titles = [] if is_series else [entry.get("originalTitle")]
    alt_titles += [a.get("title") for a in entry.get("alternateTitles") or []]
    media.alt_titles = "\n".join(t for t in alt_titles if t and t != media.title)
    if is_series:
        media.tvdb_id = entry.get("tvdbId") or media.tvdb_id
    else:
        media.tmdb_id = entry.get("tmdbId") or media.tmdb_id
        media.imdb_id = entry.get("imdbId") or media.imdb_id


async def _rebuild_import_issues(
    session: Session,
    settings: Settings,
    media: Media,
    target: Any,
    arr_id: int,
    is_series: bool,
) -> list[ImportIssue]:
    """File d'attente de l'instance du média uniquement. Échec silencieux :
    une file injoignable ne doit pas faire échouer l'analyse."""
    records: list[dict[str, Any]] = []
    try:
        client = target.sonarr() if is_series else target.radarr()
        key = "seriesId" if is_series else "movieId"
        records = index_queue_issues(await client.get_queue(), key).get(arr_id, [])
    except Exception:  # noqa: BLE001 - informatif
        records = []

    session.exec(delete(ImportIssue).where(ImportIssue.media_id == media.id))
    rows = issue_rows_for(records)
    for row in rows:
        row.media_id = media.id
        session.add(row)
    session.commit()
    return rows


async def _rebuild_files(
    session: Session,
    settings: Settings,
    media: Media,
    target: Any,
    entry: dict[str, Any],
    is_series: bool,
) -> list[MediaFile]:
    """Refait les fichiers de bibliothèque du média à partir du serveur
    multimédia et de Sonarr/Radarr, exactement comme le scan complet."""
    emby = media_server_client(settings)
    rows: list[MediaFile] = []

    if is_series:
        sonarr = target.sonarr()
        episode_files = await sonarr.get_episode_files(entry["id"])
        item, episodes = await _series_item(emby, media, episode_files)
        if item is not None:
            _apply_media_server_item(media, item)
            episode_files_by_id = {f["id"]: f for f in episode_files if f.get("id")}
            current_paths = {f["path"] for f in episode_files if f.get("path")}
            media.episode_count = len(episodes)

            sonarr_by_label: dict[str, tuple[int, int | None]] = {}
            downloaded: set[str] = set()
            try:
                for e in await sonarr.get_episodes(entry["id"]):
                    if e.get("seasonNumber") is None or e.get("episodeNumber") is None:
                        continue
                    label = f"S{e['seasonNumber']:02d}E{e['episodeNumber']:02d}"
                    if e.get("hasFile"):
                        downloaded.add(label)
                    sonarr_by_label[label] = (e["id"], e.get("episodeFileId"))
            except Exception:  # noqa: BLE001 - informatif, comme pendant un scan
                pass

            sources_by_label: dict[str, list[dict[str, Any]]] = {}
            spans: dict[str, set[str]] = {}
            for episode in episodes:
                label = _episode_label(episode)
                sources_by_label.setdefault(label, []).extend(_media_sources(episode))
                spans.setdefault(label, set()).update(_episode_span_labels(episode))

            for label, sources in sources_by_label.items():
                episode_id, episode_file_id = sonarr_by_label.get(label, (None, None))
                episode_file = episode_files_by_id.get(episode_file_id) if episode_file_id else None
                if episode_file is not None:
                    flags = _current_flags([(s.get("Path"), s.get("Size")) for s in sources], episode_file)
                else:
                    flags = [bool(s.get("Path") and s.get("Path") in current_paths) for s in sources]
                for source, is_current in zip(sources, flags):
                    rows.append(_file_row(media, source, label, is_current, episode_file_id, episode_id))
            media.missing_emby_episodes = ",".join(
                _missing_emby_labels(downloaded, [r.episode_label for r in rows], spans)
            )
        else:
            # Série disparue du serveur multimédia : la fiche ne garde pas un
            # lien mort, le statut « absent » prend le relais.
            _clear_media_server_item(media)
            media.missing_emby_episodes = ""
    else:
        movie_file = entry.get("movieFile") or None
        item = await _movie_item(emby, media, movie_file)
        if item is not None:
            _apply_media_server_item(media, item)
            sources = _without_other_instance_files(_media_sources(item), movie_file, [])
            flags = _current_flags([(s.get("Path"), s.get("Size")) for s in sources], movie_file)
            for source, is_current in zip(sources, flags):
                rows.append(_file_row(media, source, None, is_current, (movie_file or {}).get("id"), None))
        else:
            _clear_media_server_item(media)

    session.exec(delete(MediaFile).where(MediaFile.media_id == media.id))
    for row in rows:
        row.media_id = media.id
        session.add(row)
    session.commit()
    return rows


def _file_row(
    media: Media,
    source: dict[str, Any],
    label: str | None,
    is_current: bool,
    arr_file_id: int | None,
    episode_id: int | None,
) -> MediaFile:
    path = source.get("Path")
    inode = stat_inode(path)
    return MediaFile(
        media_id=media.id or 0,
        path=path or "",
        size=source.get("Size"),
        inode=inode[0] if inode else None,
        device=inode[1] if inode else None,
        episode_label=label,
        is_current=is_current,
        sonarr_episode_id=episode_id if is_current else None,
        arr_file_id=arr_file_id if is_current else None,
    )


def _clear_media_server_item(media: Media) -> None:
    media.emby_item_id = None
    media.has_poster = False
    media.poster_image_tag = None


def _apply_media_server_item(media: Media, item: dict[str, Any]) -> None:
    media.emby_item_id = item.get("Id")
    media.has_poster = bool(item.get("Id"))
    media.poster_image_tag = (item.get("ImageTags") or {}).get("Primary")
    media.emby_date_added = parse_emby_date(item.get("DateCreated"))


async def _movie_item(emby: EmbyClient, media: Media, movie_file: dict[str, Any] | None) -> dict[str, Any] | None:
    """Item du serveur multimédia pour ce film : par identifiant connu d'abord
    (un seul appel), sinon en repli par identifiant TMDB/IMDb sur la
    bibliothèque — un film absent du dernier scan peut y être arrivé depuis."""
    if media.emby_item_id:
        items = await emby.get_items_by_ids([media.emby_item_id])
        if items:
            return items[0]
    candidates = [
        item
        for item in await emby.get_library_items("Movie")
        if _matches_provider(item, {"tmdb": media.tmdb_id, "imdb": media.imdb_id})
    ]
    return _pick_emby_item(candidates, [movie_file] if movie_file else [])


async def _series_item(
    emby: EmbyClient, media: Media, episode_files: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if media.emby_item_id:
        items = await emby.get_items_by_ids([media.emby_item_id])
        if items:
            return items[0], await emby.get_episodes(items[0]["Id"])
    candidates = [
        item
        for item in await emby.get_library_items("Series")
        if _matches_provider(item, {"tvdb": media.tvdb_id})
    ]
    if not candidates:
        return None, []
    return await _pick_series_item(emby, candidates, episode_files)


def _matches_provider(item: dict[str, Any], wanted: dict[str, Any]) -> bool:
    provider_ids = {k.lower(): str(v) for k, v in (item.get("ProviderIds") or {}).items() if v}
    return any(value and provider_ids.get(key) == str(value) for key, value in wanted.items())


def _candidate_positions(
    settings: Settings,
    media: Media,
    listed: list[dict[str, Any]],
    known_hashes: dict[str, int],
) -> list[int]:
    """Torrents à réexaminer pour CE média : les siens, ceux qui ne sont
    rattachés à aucun média, ceux dont le chemin part de son dossier racine, et
    ceux dont le nom commence par son titre. Les torrents d'un autre média sont
    laissés tranquilles — seul un scan complet peut les déplacer."""
    titles = [normalize_words(media.title)]
    titles += [normalize_words(t) for t in (media.alt_titles or "").split("\n") if t]
    root = media.root_path if media.root_path else None
    usable_root = bool(root) and is_usable_root(root, settings.qbittorrent_download_path)

    positions: list[int] = []
    for pos, raw in enumerate(listed):
        torrent_hash = str(raw.get("hash") or "").lower()
        owner = known_hashes.get(torrent_hash)
        if owner is not None and owner != media.id:
            continue
        if owner == media.id:
            positions.append(pos)
            continue
        content_path = raw.get("content_path") or raw.get("save_path") or ""
        if usable_root and content_path.startswith(root or ""):
            positions.append(pos)
            continue
        release_words = normalize_release_words(str(raw.get("name") or ""))
        if any(words and release_words[: len(words)] == words for words in titles):
            positions.append(pos)
            continue
        if owner is None:
            # Torrent rattaché à aucun média : candidat naturel, c'est le cas
            # d'un torrent tout juste ajouté.
            positions.append(pos)
    return positions


async def _rebuild_torrents(
    session: Session, settings: Settings, media: Media, files: list[MediaFile]
) -> list[Torrent]:
    """Rattache les torrents candidats à ce média. Le détail (fichiers,
    trackers) n'est demandé que pour eux."""
    if not torrent_client_configured(settings):
        session.exec(delete(Torrent).where(Torrent.media_id == media.id))
        session.commit()
        return []

    known_hashes = {
        t.hash.lower(): t.media_id for t in session.exec(select(Torrent)).all() if t.hash
    }

    async with torrent_client(settings) as client:
        listed = await client.get_torrents()
        positions = _candidate_positions(settings, media, listed, known_hashes)
        fetched = FetchedTorrents()
        for pos in positions:
            raw = listed[pos]
            trackers: list[dict[str, Any]] = []
            files_raw: list[dict[str, Any]] = []
            try:
                trackers = await client.get_trackers(raw["hash"])
            except Exception:  # noqa: BLE001 - un tracker illisible n'arrête pas l'analyse
                trackers = []
            try:
                files_raw = await client.get_files(raw["hash"])
            except Exception:  # noqa: BLE001
                files_raw = []
            _append_torrent(fetched, raw, trackers, files_raw)

    view = MediaView(
        media_type=media.media_type,
        title=media.title,
        year=media.year,
        alt_titles=[t for t in (media.alt_titles or "").split("\n") if t],
        root_path=media.root_path,
        files=files,
    )
    # Rattachement à un seul média : `hash_to_index` reprend ce que le scan
    # complet avait établi (historique Sonarr/Radarr compris) pour ses propres
    # torrents.
    hash_to_index = {h: 0 for h, owner in known_hashes.items() if owner == media.id}
    attached = attach_torrents(settings, [view], fetched, hash_to_index)[0]

    session.exec(delete(Torrent).where(Torrent.media_id == media.id))
    for torrent in attached:
        torrent.media_id = media.id
        session.add(torrent)
    session.commit()
    return attached


def _append_torrent(
    fetched: FetchedTorrents,
    raw: dict[str, Any],
    trackers: list[dict[str, Any]],
    files_raw: list[dict[str, Any]],
) -> None:
    """Construit la même ligne Torrent que `torrent_match.fetch_torrents`, pour
    un seul torrent."""
    content_path = raw.get("content_path") or raw.get("save_path")
    save_path = raw.get("save_path")

    file_entries: list[tuple[str, int | None]] = []
    for f in files_raw:
        rel = f.get("name")
        if rel and save_path:
            file_entries.append((os.path.join(save_path, rel), f.get("size")))
    if not file_entries and content_path:
        file_entries.append((content_path, raw.get("size")))

    resolved_inodes: list[tuple[int, int]] = []
    file_details: list[tuple[str, int | None]] = []
    for path, size in file_entries:
        inode = stat_inode(path)
        if inode is not None:
            resolved_inodes.append(inode)
        file_details.append((os.path.basename(path), size))

    first_inode = resolved_inodes[0] if resolved_inodes else None
    domains = []
    for tr in trackers:
        domain = extract_tracker_domain(tr.get("url", ""))
        if domain:
            domains.append({"domain": domain, "status": status_label(tr.get("status", -1))})

    fetched.rows.append(
        Torrent(
            media_id=0,
            hash=raw["hash"],
            name=raw.get("name", ""),
            save_path=save_path,
            content_path=raw.get("content_path"),
            category=raw.get("category") or None,
            size=raw.get("size"),
            inode=first_inode[0] if first_inode else None,
            device=first_inode[1] if first_inode else None,
            ratio=raw.get("ratio"),
            seeders=raw.get("num_seeds"),
            leechers=raw.get("num_leechs"),
            added_on=epoch_to_datetime(raw.get("added_on")),
            completed_on=epoch_to_datetime(raw.get("completion_on")),
            trackers_json=json.dumps(domains),
        )
    )
    fetched.content_paths.append(content_path)
    fetched.file_inodes.append(resolved_inodes)
    fetched.file_details.append(file_details)
