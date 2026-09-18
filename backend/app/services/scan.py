import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlmodel import Session, delete, select

from app.clients.emby import EmbyClient, media_server_name
from app.clients.torrent import (
    TorrentAuthError,
    torrent_client,
    torrent_client_configured,
    torrent_client_name,
)
from app.database import engine
from app.clients.seer import SeerClient
from app.models.media import (
    EmbyUser,
    Media,
    MediaFile,
    MediaRequest,
    MediaType,
    MediaWatch,
    ScanRun,
    ScanStatus,
    Torrent,
)
from app.models.settings import Settings
from app.services.arr_instances import ArrTarget, arr_targets
from app.services.events import scan_events
from app.services.hardlink import episode_label_from_filename, resolve_current_files, stat_inode
from app.services.notifications import (
    ChannelTarget,
    channel_targets,
    detection_notification,
    notification_language,
    notify,
    scan_completed_notification,
    scan_failed_notification,
)
from app.services.seer import build_request_rows, index_requests, seer_configured
from app.services.trackers import extract_tracker_domain, status_label
from app.services.watch_stats import (
    apply_aggregates,
    build_watch_rows,
    collect_watch_data,
    excluded_user_ids,
    parse_emby_date,
    users_from_api,
)

logger = logging.getLogger("analysarr.scan")
_scan_lock = asyncio.Lock()

# Statuts dont l'APPARITION est notifiable (événement -> statut calculé au scan).
DETECTION_EVENTS = {
    "orphan_detected": "orphelin_qbit",
    "duplicate_detected": "doublon",
    "non_hardlink_detected": "non_hardlink",
}


@dataclass
class MediaBuildResult:
    media: Media
    files: list[MediaFile] = field(default_factory=list)
    torrents: list[Torrent] = field(default_factory=list)
    root_path: str | None = None
    # Titres alternatifs (titre original Radarr, alternateTitles Radarr/Sonarr)
    # — utilisés par la passe 3 de rattachement par nom (voir plus bas) : un
    # torrent nommé d'après le titre original anglais ("Vantage Point") doit
    # matcher un média dont Radarr affiche le titre localisé ("Angles
    # d'attaque"), pas seulement le titre principal.
    alt_titles: list[str] = field(default_factory=list)
    # Séries uniquement : épisodes que Sonarr a téléchargés mais qu'Emby n'a
    # pas repris dans sa bibliothèque (import manqué sur CES épisodes-là
    # seulement — la série elle-même est bien dans Emby). Voir la boucle
    # séries plus bas et compute_statuses.
    missing_emby_episodes: list[str] = field(default_factory=list)
    # État de visionnage par utilisateur Emby (voir services/watch_stats.py).
    watches: list[MediaWatch] = field(default_factory=list)
    # Demandes Seer rattachées (voir services/seer.py).
    requests: list[MediaRequest] = field(default_factory=list)


def current_files_size(files: list[MediaFile]) -> int:
    """Poids réellement occupé par le média : ses fichiers actuellement suivis
    (hors doublons), ou tous ses fichiers si aucun n'a pu être identifié."""
    current = [f for f in files if f.is_current] or files
    return sum(f.size or 0 for f in current)


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
    local_inode = stat_inode(path)
    arr_inode = stat_inode(arr_path) if arr_path else None
    if local_inode is not None and arr_inode is not None:
        return 2 if local_inode == arr_inode else 0
    local_parts = _path_parts(path)
    arr_parts = _path_parts(arr_path or arr_file.get("relativePath") or "")
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
_YEAR_PATTERN = re.compile(r"\b(19\d{2}|20\d{2})\b")


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


async def run_scan(trigger: str = "manual") -> None:
    if _scan_lock.locked():
        return
    async with _scan_lock:
        await _run_scan_impl(trigger)


async def _run_scan_impl(trigger: str = "manual") -> None:
    with Session(engine) as session:
        settings = session.get(Settings, 1)
        if settings is not None:
            # Détaché explicitement pour rester utilisable après la fermeture de la session
            # (sinon SQLAlchemy expire l'instance à la fermeture et tout accès lève
            # DetachedInstanceError).
            session.expunge(settings)
        radarr_targets = arr_targets(session, settings, "radarr")
        sonarr_targets = arr_targets(session, settings, "sonarr")
        channels = channel_targets(session)
        run = ScanRun(status=ScanStatus.running, trigger=trigger)
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    await scan_events.publish({"type": "started", "run_id": run_id})

    if settings is None:
        await _fail_scan(run_id, "Aucune configuration enregistrée.", channels, settings)
        return

    missing = [
        name
        for name, ok in [
            (media_server_name(settings), bool(settings.emby_url and settings.emby_api_key)),
            ("Sonarr", bool(settings.sonarr_url and settings.sonarr_api_key)),
            ("Radarr", bool(settings.radarr_url and settings.radarr_api_key)),
            (
                torrent_client_name(settings),
                torrent_client_configured(settings),
            ),
        ]
        if not ok
    ]
    if missing:
        await _fail_scan(run_id, f"Services non configurés : {', '.join(missing)}.", channels, settings)
        return

    try:
        results, qbit_torrent_count, emby_users = await _collect(settings, run_id, radarr_targets, sonarr_targets)
    except Exception as exc:  # noqa: BLE001 - toute erreur externe doit être reportée proprement, pas planter le process
        await _fail_scan(run_id, f"{type(exc).__name__} : {exc}", channels, settings)
        return

    await scan_events.publish({"type": "progress", "run_id": run_id, "stage": "enregistrement"})

    with Session(engine) as session:
        # Avant de remplacer le cache : quels médias portaient DÉJÀ chaque
        # statut ? Seule une NOUVELLE apparition est notifiée. Clé stable d'un
        # scan à l'autre : les ids sont régénérés.
        previous_medias = list(session.exec(select(Media)).all())
        previously_flagged = {
            event: {_media_key(media) for media in previous_medias if status in media.statuses.split(",")}
            for event, status in DETECTION_EVENTS.items()
        }
        session.exec(delete(MediaRequest))
        session.exec(delete(MediaWatch))
        session.exec(delete(EmbyUser))
        session.exec(delete(Torrent))
        session.exec(delete(MediaFile))
        session.exec(delete(Media))
        session.commit()

        for user in emby_users:
            session.add(user)
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
            for w in result.watches:
                w.media_id = result.media.id
                session.add(w)
            for r in result.requests:
                r.media_id = result.media.id
                session.add(r)
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
        summary = scan_completed_notification(
            notification_language(settings),
            media=run.media_count,
            duplicates=run.duplicate_count,
            orphans=run.orphan_count,
            reclaimable_bytes=sum(r.media.reclaimable_bytes for r in results),
            matched=run.qbittorrent_matched_count,
            torrents=run.qbittorrent_torrent_count,
            duration_seconds=_duration_seconds(run.started_at, run.finished_at),
        )

    await scan_events.publish({"type": "completed", "run_id": run_id, **counts})
    notify(channels, "scan_completed", summary)
    for event, status in DETECTION_EVENTS.items():
        newly_flagged = [
            (result.media.title, result.media.reclaimable_bytes)
            for result in results
            if status in result.media.statuses.split(",") and _media_key(result.media) not in previously_flagged[event]
        ]
        if newly_flagged:
            notify(channels, event, detection_notification(notification_language(settings), event, newly_flagged))
    await _run_automations(channels)


def _duration_seconds(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    # Relus depuis SQLite : naïfs (UTC), comparés sans fuseau.
    return max(0, int((finished_at.replace(tzinfo=None) - started_at.replace(tzinfo=None)).total_seconds()))


async def _run_automations(channels: list[ChannelTarget]) -> None:
    """Règles d'automatisation activées, sur les statuts que ce scan vient de
    calculer. Import différé : automations.py dépend des services d'action,
    qui dépendent eux-mêmes de ce module."""
    from app.services.automations import run_automations

    with Session(engine) as session:
        try:
            await run_automations(session, session.get(Settings, 1), channels)
        except Exception:  # noqa: BLE001 - une règle défaillante ne doit jamais faire échouer le scan
            logger.exception("Échec d'une automatisation après le scan")


def _media_key(media: Media) -> tuple[str, str, int | None]:
    """Identité d'un média d'un scan à l'autre : les ids de la table sont
    régénérés à chaque scan."""
    return media.media_type.value, media.title, media.year


async def _fail_scan(
    run_id: int, message: str, channels: list[ChannelTarget], settings: Settings | None = None
) -> None:
    notify(channels, "scan_failed", scan_failed_notification(notification_language(settings), message))
    with Session(engine) as session:
        run = session.get(ScanRun, run_id)
        if run:
            run.status = ScanStatus.failed
            run.error_message = message
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            session.commit()
    await scan_events.publish({"type": "failed", "run_id": run_id, "message": message})


async def _collect(
    settings: Settings, run_id: int, radarr_targets: list[ArrTarget], sonarr_targets: list[ArrTarget]
) -> tuple[list[MediaBuildResult], int, list[EmbyUser]]:
    """Instances Radarr/Sonarr : la principale d'abord, puis les
    supplémentaires (voir services/arr_instances.py). Chaque film/série suivi
    par une instance donne un média distinct."""
    assert settings.emby_url and settings.emby_api_key
    assert radarr_targets and sonarr_targets
    assert torrent_client_configured(settings)

    emby = EmbyClient(settings.emby_url, settings.emby_api_key, settings.media_server)

    async def progress(stage: str) -> None:
        await scan_events.publish({"type": "progress", "run_id": run_id, "stage": stage})

    await progress("radarr")
    movie_entries = [(target, movie) for target in radarr_targets for movie in await target.radarr().get_movies()]

    await progress("sonarr")
    series_entries = [(target, series) for target in sonarr_targets for series in await target.sonarr().get_series()]

    await progress("emby")
    emby_movies = await emby.get_library_items("Movie")
    emby_series = await emby.get_library_items("Series")

    emby_movies_by_tmdb = _index_items(emby_movies, "Tmdb")
    emby_movies_by_imdb = _index_items(emby_movies, "Imdb")
    emby_series_by_tvdb = _index_items(emby_series, "Tvdb")

    # Un même film/une même série peut être suivi par plusieurs instances
    # (ex : Radarr et Radarr 4K) : fichiers suivis par chaque instance, pour ne
    # jamais compter la version d'une autre instance comme un doublon.
    movie_files_by_tmdb: dict[str, list[tuple[ArrTarget, dict[str, Any]]]] = {}
    for target, movie in movie_entries:
        if movie.get("hasFile") and movie.get("movieFile") and movie.get("tmdbId"):
            movie_files_by_tmdb.setdefault(str(movie["tmdbId"]), []).append((target, movie["movieFile"]))
    series_by_tvdb: dict[str, list[tuple[ArrTarget, int]]] = {}
    for target, series in series_entries:
        if (series.get("statistics") or {}).get("episodeFileCount") and series.get("tvdbId"):
            series_by_tvdb.setdefault(str(series["tvdbId"]), []).append((target, series["id"]))

    episode_files_cache: dict[tuple[int | None, int], list[dict[str, Any]]] = {}

    async def episode_files_for(target: ArrTarget, series_id: int) -> list[dict[str, Any]]:
        key = (target.instance_id, series_id)
        if key not in episode_files_cache:
            try:
                episode_files_cache[key] = await target.sonarr().get_episode_files(series_id)
            except Exception:  # noqa: BLE001 - purement informatif pour is_current, ne doit pas bloquer le scan
                episode_files_cache[key] = []
        return episode_files_cache[key]

    results: list[MediaBuildResult] = []

    # --- Films -----------------------------------------------------------
    for target, movie in movie_entries:
        if not movie.get("hasFile"):
            continue  # pas encore téléchargé : rien à analyser pour ce film
        media = Media(
            media_type=MediaType.movie,
            title=movie.get("title") or "Sans titre",
            year=movie.get("year"),
            radarr_id=movie.get("id"),
            arr_instance_id=target.instance_id,
            tmdb_id=movie.get("tmdbId"),
            imdb_id=movie.get("imdbId"),
        )
        alt_titles = [movie.get("originalTitle")]
        alt_titles += [a.get("title") for a in movie.get("alternateTitles") or []]
        result = MediaBuildResult(
            media=media,
            root_path=movie.get("path"),
            alt_titles=[t for t in alt_titles if t and t != media.title],
        )

        movie_file = movie.get("movieFile") or None
        current_movie_file_id = (movie_file or {}).get("id")

        tmdb_key = str(movie.get("tmdbId")) if movie.get("tmdbId") else None
        candidates = (emby_movies_by_tmdb.get(tmdb_key, []) if tmdb_key else []) or emby_movies_by_imdb.get(
            movie.get("imdbId") or "", []
        )
        emby_item = _pick_emby_item(candidates, [movie_file] if movie_file else [])
        if emby_item:
            media.emby_item_id = emby_item.get("Id")
            media.has_poster = bool(emby_item.get("Id"))
            media.poster_image_tag = (emby_item.get("ImageTags") or {}).get("Primary")
            media.emby_date_added = parse_emby_date(emby_item.get("DateCreated"))
            other_files = [
                f for other, f in movie_files_by_tmdb.get(tmdb_key or "", []) if other.instance_id != target.instance_id
            ]
            sources = _without_other_instance_files(_media_sources(emby_item), movie_file, other_files)
            flags = _current_flags([(s.get("Path"), s.get("Size")) for s in sources], movie_file)
            for source, is_current in zip(sources, flags):
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
                        is_current=is_current,
                        # Seul le fichier actuel correspond à un movieFile
                        # Radarr réel — les autres sont des doublons non
                        # suivis par Radarr, rien à supprimer côté Radarr.
                        arr_file_id=current_movie_file_id if is_current else None,
                    )
                )
        results.append(result)

    # --- Séries ------------------------------------------------------------
    for target, series in series_entries:
        if not (series.get("statistics") or {}).get("episodeFileCount"):
            continue  # aucun épisode téléchargé : rien à analyser pour cette série
        sonarr = target.sonarr()
        media = Media(
            media_type=MediaType.series,
            title=series.get("title") or "Sans titre",
            year=series.get("year"),
            sonarr_id=series.get("id"),
            arr_instance_id=target.instance_id,
            tvdb_id=series.get("tvdbId"),
        )
        alt_titles = [a.get("title") for a in series.get("alternateTitles") or []]
        result = MediaBuildResult(
            media=media,
            root_path=series.get("path"),
            alt_titles=[t for t in alt_titles if t and t != media.title],
        )

        tvdb_key = str(series.get("tvdbId")) if series.get("tvdbId") else None
        candidates = emby_series_by_tvdb.get(tvdb_key, []) if tvdb_key else []
        if candidates:
            episode_files = await episode_files_for(target, series["id"])
            current_paths: set[str] = {f["path"] for f in episode_files if f.get("path")}
            episode_files_by_id: dict[int, dict[str, Any]] = {f["id"]: f for f in episode_files if f.get("id")}
            other_files = [
                f
                for other, other_series_id in series_by_tvdb.get(tvdb_key or "", [])
                if other.instance_id != target.instance_id
                for f in await episode_files_for(other, other_series_id)
            ]

            emby_item, episodes = await _pick_series_item(emby, candidates, episode_files)
            media.emby_item_id = emby_item.get("Id")
            media.has_poster = bool(emby_item.get("Id"))
            media.poster_image_tag = (emby_item.get("ImageTags") or {}).get("Primary")
            media.emby_date_added = parse_emby_date(emby_item.get("DateCreated"))

            # Épisodes que Sonarr considère téléchargés (episodeFile existant),
            # indépendamment de ce qu'Emby en a repris — voir plus bas. Sert
            # aussi à rattacher chaque MediaFile "actuel" à son identité
            # Sonarr (episode_id pour le monitoring, episodeFileId pour la
            # suppression du fichier) — voir routers/media.py, delete-selection.
            sonarr_downloaded_labels: set[str] = set()
            sonarr_by_label: dict[str, tuple[int, int | None]] = {}
            try:
                sonarr_episodes = await sonarr.get_episodes(series["id"])
                for e in sonarr_episodes:
                    if e.get("seasonNumber") is None or e.get("episodeNumber") is None:
                        continue
                    label = f"S{e['seasonNumber']:02d}E{e['episodeNumber']:02d}"
                    if e.get("hasFile"):
                        sonarr_downloaded_labels.add(label)
                    sonarr_by_label[label] = (e["id"], e.get("episodeFileId"))
            except Exception:  # noqa: BLE001 - purement informatif, ne doit pas bloquer le scan
                pass

            media.episode_count = len(episodes)
            # Fichiers regroupés par épisode : un doublon peut être un item Emby
            # distinct du même épisode, pas seulement une seconde source.
            sources_by_label: dict[str, list[dict[str, Any]]] = {}
            for episode in episodes:
                sources_by_label.setdefault(_episode_label(episode), []).extend(_media_sources(episode))
            for label, sources in sources_by_label.items():
                sonarr_episode_id, sonarr_episode_file_id = sonarr_by_label.get(label, (None, None))
                episode_file = episode_files_by_id.get(sonarr_episode_file_id) if sonarr_episode_file_id else None
                sources = _without_other_instance_files(sources, episode_file, other_files)
                if episode_file is not None:
                    flags = _current_flags([(s.get("Path"), s.get("Size")) for s in sources], episode_file)
                else:
                    flags = [bool(s.get("Path") and s.get("Path") in current_paths) for s in sources]
                for source, is_current in zip(sources, flags):
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
                            is_current=is_current,
                            # Comme pour les films : seul le fichier actuel
                            # correspond à l'episodeFile Sonarr réel.
                            sonarr_episode_id=sonarr_episode_id if is_current else None,
                            arr_file_id=sonarr_episode_file_id if is_current else None,
                        )
                    )

            # La série ELLE-MÊME est bien dans Emby (sinon on ne serait pas
            # dans cette branche), mais certains épisodes téléchargés par
            # Sonarr peuvent manquer côté Emby (import manqué) sans que ça ne
            # se voie autrement : aucun MediaFile n'est créé pour eux plus
            # haut puisque la boucle ne parcourt que ce qu'Emby a renvoyé.
            emby_labels = {f.episode_label for f in result.files if f.episode_label}
            result.missing_emby_episodes = sorted(sonarr_downloaded_labels - emby_labels)
        results.append(result)

    # --- Correspondance torrent -> média via l'historique Sonarr/Radarr ---
    # (indexé par id Radarr/Sonarr, pas par position : certains films/séries
    # sans fichier ont été exclus de `results` plus haut)
    await progress("historique")
    # Clé (instance, id) : les ids Sonarr/Radarr de deux instances se recouvrent.
    radarr_id_to_index = {
        (r.media.arr_instance_id, r.media.radarr_id): i for i, r in enumerate(results) if r.media.radarr_id is not None
    }
    sonarr_id_to_index = {
        (r.media.arr_instance_id, r.media.sonarr_id): i for i, r in enumerate(results) if r.media.sonarr_id is not None
    }

    hash_to_index: dict[str, int] = {}
    for target, movie in movie_entries:
        index = radarr_id_to_index.get((target.instance_id, movie.get("id")))
        if index is None:
            continue
        try:
            history = await target.radarr().get_history_for_movie(movie["id"])
        except Exception:  # noqa: BLE001 - un échec d'historique ne doit pas interrompre le scan
            history = []
        for event in history:
            download_id = event.get("downloadId")
            if download_id:
                hash_to_index[download_id.lower()] = index

    for target, series in series_entries:
        index = sonarr_id_to_index.get((target.instance_id, series.get("id")))
        if index is None:
            continue
        try:
            history = await target.sonarr().get_history_for_series(series["id"])
        except Exception:  # noqa: BLE001
            history = []
        for event in history:
            download_id = event.get("downloadId")
            if download_id:
                hash_to_index[download_id.lower()] = index

    # --- Torrents qBittorrent ------------------------------------------
    await progress("qbittorrent")
    try:
        async with torrent_client(settings) as qbit:
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
    except TorrentAuthError as exc:
        raise RuntimeError(f"Authentification {torrent_client_name(settings)} refusée pendant le scan : {exc}") from exc

    torrent_rows: list[Torrent] = []
    torrent_content_paths: list[str | None] = []
    # Inodes de CHAQUE fichier du torrent (pas un seul par torrent) : un pack
    # saison est un torrent multi-fichiers dont `content_path` ne désigne que
    # le dossier racine — comparer l'inode de ce dossier à celui d'un épisode
    # ne peut jamais correspondre. Il faut regarder chaque fichier du torrent.
    torrent_file_inodes: list[list[tuple[int, int]]] = []
    # (nom de fichier, taille annoncée par qBittorrent) pour chaque fichier du
    # torrent — sert à détecter, pour un torrent non hardlinké, s'il s'agit
    # tout de même du même contenu qu'un fichier actuel (même épisode, même
    # taille en octets) plutôt qu'une ancienne version : voir plus bas.
    torrent_file_details: list[list[tuple[str, int | None]]] = []
    for t in torrents:
        content_path = t.get("content_path") or t.get("save_path")
        save_path = t.get("save_path")

        file_entries: list[tuple[str, int | None]] = []
        files = files_by_hash.get(t["hash"], [])
        for f in files:
            rel = f.get("name")
            if rel and save_path:
                file_entries.append((os.path.join(save_path, rel), f.get("size")))
        if not file_entries and content_path:
            # repli si l'API torrents/files a échoué ou n'a rien renvoyé
            file_entries.append((content_path, t.get("size")))

        resolved_inodes: list[tuple[int, int]] = []
        file_details: list[tuple[str, int | None]] = []
        for path, size in file_entries:
            inode = stat_inode(path)
            if inode is not None:
                resolved_inodes.append(inode)
            file_details.append((os.path.basename(path), size))
        torrent_file_inodes.append(resolved_inodes)
        torrent_file_details.append(file_details)

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
                category=t.get("category") or None,
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
        # Titre principal ET titres alternatifs (titre original Radarr,
        # alternateTitles Radarr/Sonarr) : un torrent nommé d'après le titre
        # original anglais ("Vantage Point") doit pouvoir matcher un média
        # dont Radarr affiche le titre localisé ("Angles d'attaque").
        media_titles: list[tuple[int, list[str]]] = []
        for i, r in enumerate(results):
            if r.media.title:
                media_titles.append((i, _normalize_words(r.media.title)))
            for alt in r.alt_titles:
                media_titles.append((i, _normalize_words(alt)))
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
                if media.media_type == MediaType.movie and media.year:
                    # Un titre de film court/générique ("Dune", "Avatar") peut
                    # préfixer aussi bien le film que sa suite/son remake — si
                    # UNE année apparaît dans le nom du torrent, elle doit
                    # correspondre à celle du média. Si le nom n'en contient
                    # aucune (fréquent, ex: "Vantage.Point.1080p...-FHD"), on
                    # ne peut simplement pas trancher par l'année : le titre
                    # (éventuellement un titre alternatif, voir alt_titles)
                    # fait alors seul foi.
                    year_in_name = _YEAR_PATTERN.search(torrent_rows[pos].name)
                    if year_in_name and int(year_in_name.group(1)) != media.year:
                        continue
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

        if torrent_row.is_hardlinked is False:
            # Non hardlinké : vrai orphelin (ancienne version remplacée par un
            # upgrade), ou simple copie non hardlinkée du fichier actuel (ex :
            # ajout antérieur à la mise en place du hardlink sur le serveur) ?
            # La provenance du rattachement (historique vs nom) ne le dit pas
            # de façon fiable — seul le contenu fait foi : même épisode/même
            # média ET même taille en octets qu'un fichier actuellement suivi
            # par la bibliothèque = quasi certainement le même fichier.
            by_episode, current_single = resolve_current_files(results[index].files, results[index].media.media_type)
            for name, size in torrent_file_details[pos]:
                if size is None:
                    continue
                if results[index].media.media_type == MediaType.series:
                    label = episode_label_from_filename(name)
                    current = by_episode.get(label) if label else None
                else:
                    current = current_single
                if current is not None and current.size == size:
                    torrent_row.repairable = True
                    break

        results[index].torrents.append(torrent_row)

    # --- Calcul des statuts -------------------------------------------
    for result in results:
        result.media.missing_emby_episodes = ",".join(result.missing_emby_episodes)
        statuses, reclaimable = compute_statuses(
            result.files, result.torrents, bool(result.media.emby_item_id), len(result.missing_emby_episodes)
        )
        result.media.statuses = ",".join(sorted(statuses))
        result.media.reclaimable_bytes = reclaimable
        result.media.total_size = current_files_size(result.files)

    # --- Visionnage Emby -----------------------------------------------
    # Purement informatif : un échec (Emby trop ancien, droits insuffisants)
    # laisse les statistiques vides sans jamais faire échouer le scan.
    await progress("visionnage")
    try:
        emby_users = users_from_api(await emby.get_users())
        watch_data = await collect_watch_data(emby, emby_users)
    except (httpx.HTTPError, ValueError):
        emby_users, watch_data = [], {}
    excluded = excluded_user_ids(settings)
    for result in results:
        result.watches = build_watch_rows(result.media, emby_users, watch_data)
        apply_aggregates(result.media, result.watches, excluded)

    # --- Demandes Seer (optionnel) ---------------------------------------
    # Même principe que le visionnage : un Seer injoignable laisse les
    # demandes vides sans faire échouer le scan.
    if seer_configured(settings):
        await progress("seer")
        try:
            request_index = index_requests(await SeerClient(settings.seer_url, settings.seer_api_key).get_requests())
        except (httpx.HTTPError, ValueError):
            request_index = {}
        for result in results:
            result.requests = build_request_rows(result.media, request_index)

    return results, len(torrents), emby_users


def compute_statuses(
    files: list[MediaFile], torrents: list[Torrent], has_emby_item: bool, missing_emby_episode_count: int = 0
) -> tuple[set[str], int]:
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

    # Un torrent "repairable" a le même contenu (même épisode/média, même
    # taille en octets) qu'un fichier actuellement suivi par la bibliothèque,
    # juste non hardlinké — pas un vrai orphelin à supprimer, mais un
    # candidat à la réparation de hardlink (voir _collect ci-dessus).
    orphan_torrents = [t for t in torrents if t.is_hardlinked is False and not t.repairable]
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

    repairable_torrents = [t for t in torrents if t.is_hardlinked is False and t.repairable]
    if repairable_torrents:
        # Contrairement à orphelin_qbit, ce média EST bien seedé — juste pas
        # protégé par hardlink. Statut distinct pour ne pas afficher "non
        # seedé" à tort, avec son propre filtre et son action de réparation.
        statuses.add("non_hardlink")

    all_domains = {d["domain"] for t in torrents for d in json.loads(t.trackers_json)}
    if len(all_domains) == 1:
        statuses.add("tracker_unique")

    # Un média sain doit être présent à la fois dans Emby et dans qBittorrent
    # (activement protégé par un torrent, cross-seedé ou non). Pour une série,
    # "présent dans Emby" ne suffit pas à garantir que CHAQUE épisode
    # téléchargé y figure : Sonarr peut avoir un episodeFile pour un épisode
    # qu'Emby n'a jamais importé (bug d'import, bibliothèque pas rescannée...)
    # sans que la série elle-même ne soit absente d'Emby pour autant.
    if not has_emby_item or missing_emby_episode_count > 0:
        statuses.add("manquant_emby")

    has_active_torrent = any(t.is_hardlinked is True for t in torrents)
    has_unresolved_torrent = any(t.is_hardlinked is None for t in torrents)
    if not has_active_torrent and not has_unresolved_torrent and not repairable_torrents:
        # Sans torrent actif confirmé ni torrent réparable (donc bien seedé) :
        # soit aucun torrent du tout, soit tous orphelins. Si le hardlink n'a
        # pas pu être évalué (chemins non montés), on ne se prononce pas
        # plutôt que de faux positifs en masse.
        statuses.add("manquant_qbit")

    return statuses, reclaimable
