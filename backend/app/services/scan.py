import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, delete

from app.clients.arr import RadarrClient, SonarrClient
from app.clients.emby import EmbyClient
from app.clients.qbittorrent import QbittorrentAuthError, QbittorrentClient
from app.database import engine
from app.models.media import Media, MediaFile, MediaType, ScanRun, ScanStatus, Torrent
from app.models.settings import Settings
from app.services.events import scan_events
from app.services.hardlink import stat_inode
from app.services.trackers import extract_tracker_domain, status_label

_scan_lock = asyncio.Lock()


@dataclass
class MediaBuildResult:
    media: Media
    files: list[MediaFile] = field(default_factory=list)
    torrents: list[Torrent] = field(default_factory=list)
    root_path: str | None = None


def is_scan_running() -> bool:
    return _scan_lock.locked()


def _provider_id(provider_ids: dict[str, Any] | None, *keys: str) -> str | None:
    lower = {k.lower(): v for k, v in (provider_ids or {}).items()}
    for key in keys:
        value = lower.get(key.lower())
        if value:
            return str(value)
    return None


def _episode_label(item: dict[str, Any]) -> str | None:
    season = item.get("ParentIndexNumber")
    episode = item.get("IndexNumber")
    if season is None or episode is None:
        return f"item:{item.get('Id')}"
    return f"S{int(season):02d}E{int(episode):02d}"


def _media_sources(item: dict[str, Any]) -> list[dict[str, Any]]:
    sources = item.get("MediaSources") or []
    if sources:
        return sources
    path = item.get("Path")
    return [{"Path": path, "Size": None}] if path else []


async def run_scan() -> None:
    if _scan_lock.locked():
        return
    async with _scan_lock:
        await _run_scan_impl()


async def _run_scan_impl() -> None:
    with Session(engine) as session:
        settings = session.get(Settings, 1)
        if settings is not None:
            # Détaché explicitement pour rester utilisable après la fermeture de la session
            # (sinon SQLAlchemy expire l'instance à la fermeture et tout accès lève
            # DetachedInstanceError).
            session.expunge(settings)
        run = ScanRun(status=ScanStatus.running)
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    await scan_events.publish({"type": "started", "run_id": run_id})

    if settings is None:
        await _fail_scan(run_id, "Aucune configuration enregistrée.")
        return

    missing = [
        name
        for name, ok in [
            ("Emby", bool(settings.emby_url and settings.emby_api_key)),
            ("Sonarr", bool(settings.sonarr_url and settings.sonarr_api_key)),
            ("Radarr", bool(settings.radarr_url and settings.radarr_api_key)),
            (
                "qBittorrent",
                bool(settings.qbittorrent_url and settings.qbittorrent_username and settings.qbittorrent_password),
            ),
        ]
        if not ok
    ]
    if missing:
        await _fail_scan(run_id, f"Services non configurés : {', '.join(missing)}.")
        return

    try:
        results = await _collect(settings, run_id)
    except Exception as exc:  # noqa: BLE001 - toute erreur externe doit être reportée proprement, pas planter le process
        await _fail_scan(run_id, f"{type(exc).__name__} : {exc}")
        return

    await scan_events.publish({"type": "progress", "run_id": run_id, "stage": "enregistrement"})

    with Session(engine) as session:
        session.exec(delete(Torrent))
        session.exec(delete(MediaFile))
        session.exec(delete(Media))
        session.commit()

        for result in results:
            session.add(result.media)
        session.commit()
        for result in results:
            session.refresh(result.media)
            for f in result.files:
                f.media_id = result.media.id
                session.add(f)
            for t in result.torrents:
                t.media_id = result.media.id
                session.add(t)
        session.commit()

        run = session.get(ScanRun, run_id)
        assert run is not None
        run.status = ScanStatus.completed
        run.finished_at = datetime.now(timezone.utc)
        run.media_count = len(results)
        run.duplicate_count = sum(1 for r in results if "doublon" in r.media.statuses.split(","))
        run.orphan_count = sum(1 for r in results if "orphelin_qbit" in r.media.statuses.split(","))
        run.tracker_unique_count = sum(1 for r in results if "tracker_unique" in r.media.statuses.split(","))
        session.add(run)
        session.commit()
        counts = {
            "media_count": run.media_count,
            "duplicate_count": run.duplicate_count,
            "orphan_count": run.orphan_count,
        }

    await scan_events.publish({"type": "completed", "run_id": run_id, **counts})


async def _fail_scan(run_id: int, message: str) -> None:
    with Session(engine) as session:
        run = session.get(ScanRun, run_id)
        if run:
            run.status = ScanStatus.failed
            run.error_message = message
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            session.commit()
    await scan_events.publish({"type": "failed", "run_id": run_id, "message": message})


async def _collect(settings: Settings, run_id: int) -> list[MediaBuildResult]:
    assert settings.emby_url and settings.emby_api_key
    assert settings.sonarr_url and settings.sonarr_api_key
    assert settings.radarr_url and settings.radarr_api_key
    assert settings.qbittorrent_url and settings.qbittorrent_username and settings.qbittorrent_password

    emby = EmbyClient(settings.emby_url, settings.emby_api_key)
    radarr = RadarrClient(settings.radarr_url, settings.radarr_api_key)
    sonarr = SonarrClient(settings.sonarr_url, settings.sonarr_api_key)

    async def progress(stage: str) -> None:
        await scan_events.publish({"type": "progress", "run_id": run_id, "stage": stage})

    await progress("radarr")
    movies = await radarr.get_movies()

    await progress("sonarr")
    series_list = await sonarr.get_series()

    await progress("emby")
    emby_movies = await emby.get_library_items("Movie")
    emby_series = await emby.get_library_items("Series")

    emby_movie_by_tmdb = {_provider_id(m.get("ProviderIds"), "Tmdb"): m for m in emby_movies if m.get("ProviderIds")}
    emby_movie_by_imdb = {_provider_id(m.get("ProviderIds"), "Imdb"): m for m in emby_movies if m.get("ProviderIds")}
    emby_series_by_tvdb = {_provider_id(s.get("ProviderIds"), "Tvdb"): s for s in emby_series if s.get("ProviderIds")}

    results: list[MediaBuildResult] = []

    # --- Films -----------------------------------------------------------
    for movie in movies:
        media = Media(
            media_type=MediaType.movie,
            title=movie.get("title") or "Sans titre",
            year=movie.get("year"),
            radarr_id=movie.get("id"),
            tmdb_id=movie.get("tmdbId"),
            imdb_id=movie.get("imdbId"),
        )
        result = MediaBuildResult(media=media, root_path=movie.get("path"))

        current_path = (movie.get("movieFile") or {}).get("path")

        emby_item = emby_movie_by_tmdb.get(str(movie.get("tmdbId"))) or emby_movie_by_imdb.get(movie.get("imdbId"))
        if emby_item:
            media.emby_item_id = emby_item.get("Id")
            media.has_poster = bool(emby_item.get("Id"))
            for source in _media_sources(emby_item):
                path = source.get("Path")
                inode = stat_inode(path)
                result.files.append(
                    MediaFile(
                        media_id=0,
                        path=path or "",
                        size=source.get("Size"),
                        inode=inode[0] if inode else None,
                        device=inode[1] if inode else None,
                        episode_label=None,
                        is_current=bool(path and current_path and path == current_path),
                    )
                )
        results.append(result)

    # --- Séries ------------------------------------------------------------
    for series in series_list:
        media = Media(
            media_type=MediaType.series,
            title=series.get("title") or "Sans titre",
            year=series.get("year"),
            sonarr_id=series.get("id"),
            tvdb_id=series.get("tvdbId"),
        )
        result = MediaBuildResult(media=media, root_path=series.get("path"))

        tvdb_key = str(series.get("tvdbId")) if series.get("tvdbId") else None
        emby_item = emby_series_by_tvdb.get(tvdb_key) if tvdb_key else None
        if emby_item:
            media.emby_item_id = emby_item.get("Id")
            media.has_poster = bool(emby_item.get("Id"))

            current_paths: set[str] = set()
            try:
                episode_files = await sonarr.get_episode_files(series["id"])
                current_paths = {f["path"] for f in episode_files if f.get("path")}
            except Exception:  # noqa: BLE001 - purement informatif pour is_current, ne doit pas bloquer le scan
                pass

            episodes = await emby.get_episodes(emby_item["Id"])
            for episode in episodes:
                label = _episode_label(episode)
                for source in _media_sources(episode):
                    path = source.get("Path")
                    inode = stat_inode(path)
                    result.files.append(
                        MediaFile(
                            media_id=0,
                            path=path or "",
                            size=source.get("Size"),
                            inode=inode[0] if inode else None,
                            device=inode[1] if inode else None,
                            episode_label=label,
                            is_current=bool(path and path in current_paths),
                        )
                    )
        results.append(result)

    # --- Correspondance torrent -> média via l'historique Sonarr/Radarr ---
    await progress("historique")
    hash_to_index: dict[str, int] = {}
    for i, movie in enumerate(movies):
        try:
            history = await radarr.get_history_for_movie(movie["id"])
        except Exception:  # noqa: BLE001 - un échec d'historique ne doit pas interrompre le scan
            history = []
        for event in history:
            download_id = event.get("downloadId")
            if download_id:
                hash_to_index[download_id.lower()] = i

    movie_count = len(movies)
    for j, series in enumerate(series_list):
        try:
            history = await sonarr.get_history_for_series(series["id"])
        except Exception:  # noqa: BLE001
            history = []
        for event in history:
            download_id = event.get("downloadId")
            if download_id:
                hash_to_index[download_id.lower()] = movie_count + j

    # --- Torrents qBittorrent ------------------------------------------
    await progress("qbittorrent")
    try:
        async with QbittorrentClient(
            settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password
        ) as qbit:
            torrents = await qbit.get_torrents()
            trackers_by_hash: dict[str, list[dict[str, Any]]] = {}
            for t in torrents:
                try:
                    trackers_by_hash[t["hash"]] = await qbit.get_trackers(t["hash"])
                except Exception:  # noqa: BLE001
                    trackers_by_hash[t["hash"]] = []
    except QbittorrentAuthError as exc:
        raise RuntimeError(f"Authentification qBittorrent refusée pendant le scan : {exc}") from exc

    for t in torrents:
        content_path = t.get("content_path") or t.get("save_path")
        inode = stat_inode(content_path)

        domains = []
        for tr in trackers_by_hash.get(t["hash"], []):
            domain = extract_tracker_domain(tr.get("url", ""))
            if domain:
                domains.append({"domain": domain, "status": status_label(tr.get("status", -1))})

        torrent_row = Torrent(
            media_id=0,
            hash=t["hash"],
            name=t.get("name", ""),
            save_path=t.get("save_path"),
            content_path=t.get("content_path"),
            size=t.get("size"),
            inode=inode[0] if inode else None,
            device=inode[1] if inode else None,
            trackers_json=json.dumps(domains),
        )

        index = hash_to_index.get(t["hash"].lower())
        if index is None:
            # repli : le torrent contient-il le dossier racine d'un média connu ?
            for i, result in enumerate(results):
                if result.root_path and content_path and content_path.startswith(result.root_path):
                    index = i
                    break

        if index is not None:
            target = results[index]
            media_inodes = {(f.inode, f.device) for f in target.files if f.inode is not None}
            if torrent_row.inode is None or not media_inodes:
                torrent_row.is_hardlinked = None
            else:
                torrent_row.is_hardlinked = (torrent_row.inode, torrent_row.device) in media_inodes
            target.torrents.append(torrent_row)

    # --- Calcul des statuts -------------------------------------------
    for result in results:
        statuses, reclaimable = compute_statuses(result.files, result.torrents)
        result.media.statuses = ",".join(sorted(statuses))
        result.media.reclaimable_bytes = reclaimable

    return results


def compute_statuses(files: list[MediaFile], torrents: list[Torrent]) -> tuple[set[str], int]:
    statuses: set[str] = set()
    reclaimable = 0

    groups: dict[str | None, list[MediaFile]] = {}
    for f in files:
        groups.setdefault(f.episode_label, []).append(f)

    for group_files in groups.values():
        if len(group_files) <= 1:
            continue
        resolved = [(f.inode, f.device) for f in group_files if f.inode is not None]
        confirmed_distinct = bool(resolved) and len(set(resolved)) > 1
        unverifiable = not resolved
        if confirmed_distinct or unverifiable:
            statuses.add("doublon")
            sizes = sorted((f.size or 0 for f in group_files), reverse=True)
            reclaimable += sum(sizes[1:])

    orphan_torrents = [t for t in torrents if t.is_hardlinked is False]
    if orphan_torrents:
        statuses.add("orphelin_qbit")
        reclaimable += sum(t.size or 0 for t in orphan_torrents)

    all_domains = {d["domain"] for t in torrents for d in json.loads(t.trackers_json)}
    if len(all_domains) == 1:
        statuses.add("tracker_unique")

    return statuses, reclaimable
