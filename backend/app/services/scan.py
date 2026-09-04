import asyncio
import json
import os
import re
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


def _is_usable_root(root_path: str, qbittorrent_download_path: str | None) -> bool:
    """Faux si `root_path` est trop générique pour servir de repli de rattachement
    par chemin — c'est-à-dire s'il est égal à, ou un ancêtre de, la racine des
    téléchargements qBittorrent elle-même. Un tel chemin correspondrait par
    préfixe à N'IMPORTE QUEL torrent, quel que soit son média réel."""
    if not qbittorrent_download_path:
        return True
    normalized_root = root_path.rstrip("/\\")
    normalized_qbit = qbittorrent_download_path.rstrip("/\\")
    if normalized_root == normalized_qbit:
        return False
    return not (normalized_qbit.startswith(normalized_root + "/") or normalized_qbit.startswith(normalized_root + "\\"))


def _epoch_to_datetime(value: Any) -> datetime | None:
    """qBittorrent renvoie -1 (voire 0) pour un horodatage non défini."""
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


def _normalize_words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def _normalize_release_words(name: str) -> list[str]:
    """Réduit un nom de release qBittorrent à la liste de mots probablement
    issus du titre, en retirant extension, numérotation saison/épisode et tags
    qualité/codec/langue/groupe — pour un rattachement approximatif par
    préfixe de titre quand ni l'inode ni l'historique Sonarr/Radarr n'ont
    permis de rattacher le torrent à un média (typiquement un ajout manuel
    antérieur à la mise en place du hardlink sur le serveur)."""
    base = os.path.splitext(name)[0]
    base = _SEASON_EPISODE_PATTERN.sub(" ", base)
    base = _RELEASE_TAG_PATTERN.sub(" ", base)
    return _normalize_words(base)


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
        results, qbit_torrent_count = await _collect(settings, run_id)
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
        run.qbittorrent_torrent_count = qbit_torrent_count
        run.qbittorrent_matched_count = sum(len(r.torrents) for r in results)
        session.add(run)
        session.commit()
        counts = {
            "media_count": run.media_count,
            "duplicate_count": run.duplicate_count,
            "orphan_count": run.orphan_count,
            "qbittorrent_torrent_count": run.qbittorrent_torrent_count,
            "qbittorrent_matched_count": run.qbittorrent_matched_count,
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


async def _collect(settings: Settings, run_id: int) -> tuple[list[MediaBuildResult], int]:
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
        if not movie.get("hasFile"):
            continue  # pas encore téléchargé : rien à analyser pour ce film
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
        if not (series.get("statistics") or {}).get("episodeFileCount"):
            continue  # aucun épisode téléchargé : rien à analyser pour cette série
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
    # (indexé par id Radarr/Sonarr, pas par position : certains films/séries
    # sans fichier ont été exclus de `results` plus haut)
    await progress("historique")
    radarr_id_to_index = {r.media.radarr_id: i for i, r in enumerate(results) if r.media.radarr_id is not None}
    sonarr_id_to_index = {r.media.sonarr_id: i for i, r in enumerate(results) if r.media.sonarr_id is not None}

    hash_to_index: dict[str, int] = {}
    for movie in movies:
        index = radarr_id_to_index.get(movie.get("id"))
        if index is None:
            continue
        try:
            history = await radarr.get_history_for_movie(movie["id"])
        except Exception:  # noqa: BLE001 - un échec d'historique ne doit pas interrompre le scan
            history = []
        for event in history:
            download_id = event.get("downloadId")
            if download_id:
                hash_to_index[download_id.lower()] = index

    for series in series_list:
        index = sonarr_id_to_index.get(series.get("id"))
        if index is None:
            continue
        try:
            history = await sonarr.get_history_for_series(series["id"])
        except Exception:  # noqa: BLE001
            history = []
        for event in history:
            download_id = event.get("downloadId")
            if download_id:
                hash_to_index[download_id.lower()] = index

    # --- Torrents qBittorrent ------------------------------------------
    await progress("qbittorrent")
    try:
        async with QbittorrentClient(
            settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password
        ) as qbit:
            torrents = await qbit.get_torrents()
            trackers_by_hash: dict[str, list[dict[str, Any]]] = {}
            files_by_hash: dict[str, list[dict[str, Any]]] = {}
            for t in torrents:
                try:
                    trackers_by_hash[t["hash"]] = await qbit.get_trackers(t["hash"])
                except Exception:  # noqa: BLE001
                    trackers_by_hash[t["hash"]] = []
                try:
                    files_by_hash[t["hash"]] = await qbit.get_files(t["hash"])
                except Exception:  # noqa: BLE001
                    files_by_hash[t["hash"]] = []
    except QbittorrentAuthError as exc:
        raise RuntimeError(f"Authentification qBittorrent refusée pendant le scan : {exc}") from exc

    torrent_rows: list[Torrent] = []
    torrent_content_paths: list[str | None] = []
    # Inodes de CHAQUE fichier du torrent (pas un seul par torrent) : un pack
    # saison est un torrent multi-fichiers dont `content_path` ne désigne que
    # le dossier racine — comparer l'inode de ce dossier à celui d'un épisode
    # ne peut jamais correspondre. Il faut regarder chaque fichier du torrent.
    torrent_file_inodes: list[list[tuple[int, int]]] = []
    for t in torrents:
        content_path = t.get("content_path") or t.get("save_path")
        save_path = t.get("save_path")

        file_paths: list[str] = []
        files = files_by_hash.get(t["hash"], [])
        for f in files:
            rel = f.get("name")
            if rel and save_path:
                file_paths.append(os.path.join(save_path, rel))
        if not file_paths and content_path:
            # repli si l'API torrents/files a échoué ou n'a rien renvoyé
            file_paths.append(content_path)

        resolved = [stat_inode(p) for p in file_paths]
        resolved_inodes = [r for r in resolved if r is not None]
        torrent_file_inodes.append(resolved_inodes)

        first_inode = resolved_inodes[0] if resolved_inodes else None

        domains = []
        for tr in trackers_by_hash.get(t["hash"], []):
            domain = extract_tracker_domain(tr.get("url", ""))
            if domain:
                domains.append({"domain": domain, "status": status_label(tr.get("status", -1))})

        torrent_rows.append(
            Torrent(
                media_id=0,
                hash=t["hash"],
                name=t.get("name", ""),
                save_path=t.get("save_path"),
                content_path=t.get("content_path"),
                size=t.get("size"),
                inode=first_inode[0] if first_inode else None,
                device=first_inode[1] if first_inode else None,
                ratio=t.get("ratio"),
                seeders=t.get("num_seeds"),
                leechers=t.get("num_leechs"),
                added_on=_epoch_to_datetime(t.get("added_on")),
                completed_on=_epoch_to_datetime(t.get("completion_on")),
                trackers_json=json.dumps(domains),
            )
        )
        torrent_content_paths.append(content_path)

    # Index des inodes des fichiers Emby actuels -> média. C'est le signal le
    # plus fiable pour repérer un torrent protégé, quel que soit son chemin de
    # stockage réel — notamment les copies cross-seed, qui vivent souvent en
    # dehors des dossiers gérés par Sonarr/Radarr.
    emby_inode_to_index: dict[tuple[int, int], int] = {}
    for i, result in enumerate(results):
        for f in result.files:
            if f.inode is not None:
                emby_inode_to_index[(f.inode, f.device)] = i

    def _match_emby_inode(pos: int) -> int | None:
        for key in torrent_file_inodes[pos]:
            index = emby_inode_to_index.get(key)
            if index is not None:
                return index
        return None

    indices: list[int | None] = [None] * len(torrent_rows)
    protected: list[bool] = [False] * len(torrent_rows)
    inode_to_index: dict[tuple[int, int], int] = dict(emby_inode_to_index)
    unresolved: list[int] = []

    # Passe 1 : rattachement direct — inode Emby actuel (n'importe lequel des
    # fichiers du torrent), puis historique Sonarr/Radarr, puis chemin racine
    # du média en dernier recours.
    for pos, torrent_row in enumerate(torrent_rows):
        index = _match_emby_inode(pos)
        is_protected = index is not None

        if index is None:
            index = hash_to_index.get(torrent_row.hash.lower())
        if index is None:
            content_path = torrent_content_paths[pos]
            for i, result in enumerate(results):
                if (
                    result.root_path
                    and _is_usable_root(result.root_path, settings.qbittorrent_download_path)
                    and content_path
                    and content_path.startswith(result.root_path)
                ):
                    index = i
                    break

        if index is not None:
            indices[pos] = index
            protected[pos] = is_protected
            for key in torrent_file_inodes[pos]:
                inode_to_index.setdefault(key, index)
        else:
            unresolved.append(pos)

    # Passe 2 : copies cross-seed d'un torrent déjà rattaché — cross-seed (pas
    # Sonarr/Radarr) les a ajoutées, donc aucune trace dans l'historique, mais
    # elles partagent au moins un fichier (même inode) avec un torrent que la
    # passe 1 a su identifier.
    for pos in unresolved:
        for key in torrent_file_inodes[pos]:
            index = inode_to_index.get(key)
            if index is not None:
                indices[pos] = index
                protected[pos] = False  # sinon la passe 1 l'aurait déjà marqué protégé
                break

    # Passe 3 : repli par similarité de titre — pour les torrents encore non
    # identifiés (ni inode, ni historique, ni chemin), typiquement des ajouts
    # manuels antérieurs à la mise en place du hardlink sur le serveur (le
    # torrent existe bel et bien et concerne ce média, mais son fichier n'a
    # jamais été lié au fichier de la bibliothèque). Rattachement heuristique
    # uniquement : jamais marqué protégé, ce qui laisse orphelin_qbit/
    # manquant_qbit s'appliquer normalement — mais rend le torrent visible sur
    # la bonne fiche, avec une action de réparation possible.
    still_unresolved = [pos for pos in unresolved if indices[pos] is None]
    if still_unresolved:
        media_titles = [(i, _normalize_words(r.media.title)) for i, r in enumerate(results) if r.media.title]
        for pos in still_unresolved:
            release_words = _normalize_release_words(torrent_rows[pos].name)
            if not release_words:
                continue
            best: tuple[int, int] | None = None  # (longueur du titre, index média)
            for i, title_words in media_titles:
                n = len(title_words)
                if n == 0 or n > len(release_words) or release_words[:n] != title_words:
                    continue
                media = results[i].media
                if media.media_type == MediaType.movie and (
                    not media.year or str(media.year) not in torrent_rows[pos].name
                ):
                    continue  # titre de film trop générique sans confirmation par l'année
                if best is None or n > best[0]:
                    best = (n, i)
            if best is not None:
                indices[pos] = best[1]
                protected[pos] = False
                torrent_rows[pos].matched_by_name = True

    for pos, torrent_row in enumerate(torrent_rows):
        index = indices[pos]
        if index is None:
            continue
        torrent_row.is_hardlinked = None if not torrent_file_inodes[pos] else protected[pos]
        results[index].torrents.append(torrent_row)

    # --- Calcul des statuts -------------------------------------------
    for result in results:
        statuses, reclaimable = compute_statuses(result.files, result.torrents, bool(result.media.emby_item_id))
        result.media.statuses = ",".join(sorted(statuses))
        result.media.reclaimable_bytes = reclaimable

    return results, len(torrents)


def compute_statuses(files: list[MediaFile], torrents: list[Torrent], has_emby_item: bool) -> tuple[set[str], int]:
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
        # Plusieurs torrents orphelins peuvent être des copies cross-seed d'une
        # même ancienne version (même inode entre eux) : supprimer l'un d'eux
        # ne libère pas d'espace tant qu'un autre pointe encore vers ce même
        # fichier. On ne compte donc chaque inode qu'une seule fois.
        seen_inodes: set[tuple[int, int]] = set()
        for t in orphan_torrents:
            key = (t.inode, t.device) if t.inode is not None else None
            if key is not None:
                if key in seen_inodes:
                    continue
                seen_inodes.add(key)
            reclaimable += t.size or 0

    all_domains = {d["domain"] for t in torrents for d in json.loads(t.trackers_json)}
    if len(all_domains) == 1:
        statuses.add("tracker_unique")

    # Un média sain doit être présent à la fois dans Emby et dans qBittorrent
    # (activement protégé par un torrent, cross-seedé ou non).
    if not has_emby_item:
        statuses.add("manquant_emby")

    has_active_torrent = any(t.is_hardlinked is True for t in torrents)
    has_unresolved_torrent = any(t.is_hardlinked is None for t in torrents)
    if not has_active_torrent and not has_unresolved_torrent:
        # Sans torrent actif confirmé : soit aucun torrent du tout, soit tous
        # orphelins. Si le hardlink n'a pas pu être évalué (chemins non
        # montés), on ne se prononce pas plutôt que de faux positifs en masse.
        statuses.add("manquant_qbit")

    return statuses, reclaimable
