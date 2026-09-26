"""Lecture de tous les services pour un scan complet, puis rattachement des
torrents et calcul des statuts. Une fonction par source, dans l'ordre du scan."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.clients.emby import EmbyClient, media_server_client
from app.clients.torrent import torrent_client_configured
from app.models.media import EmbyUser
from app.models.settings import Settings
from app.services.arr_instances import ArrTarget
from app.services.events import scan_events
from app.services.queue_issues import index_queue_issues
from app.services.scan.library import _index_items
from app.services.scan.results import (
    LibraryContext,
    MediaBuildResult,
    build_movie_result,
    build_series_result,
    build_untracked_results,
)
from app.services.scan.statuses import apply_statuses
from app.services.seer import build_request_rows, fetch_request_index, seer_client
from app.services.torrent_match import FetchedTorrents, MediaView, attach_torrents, fetch_torrents
from app.services.watch_stats import (
    apply_aggregates,
    build_watch_rows,
    collect_watch_data,
    excluded_user_ids,
    users_from_api,
)

logger = logging.getLogger(__name__)

Progress = Callable[[str], Awaitable[None]]
ArrEntries = list[tuple[ArrTarget, dict[str, Any]]]
QueueIssues = dict[tuple[int | None, int], list[dict[str, Any]]]


async def _collect(
    settings: Settings,
    run_id: int,
    radarr_targets: list[ArrTarget],
    sonarr_targets: list[ArrTarget],
) -> tuple[list[MediaBuildResult], FetchedTorrents, list[EmbyUser]]:
    """Instances Radarr/Sonarr : la principale d'abord, puis les
    supplémentaires (voir services/arr_instances.py). Chaque film/série suivi
    par une instance donne un média distinct."""
    # Garanti par l'appelant (réglages complets) ; vérifié ici plutôt que par
    # assert, que `python -O` supprimerait.
    emby = media_server_client(settings)
    if emby is None:
        raise RuntimeError("Serveur multimédia non configuré")
    if not (radarr_targets and sonarr_targets):
        raise RuntimeError("Sonarr ou Radarr non configuré")
    if not torrent_client_configured(settings):
        raise RuntimeError("Client torrent non configuré")

    async def progress(stage: str) -> None:
        await scan_events.publish({"type": "progress", "run_id": run_id, "stage": stage})

    await progress("radarr")
    movie_entries = [(target, movie) for target in radarr_targets for movie in await target.radarr().get_movies()]
    await progress("sonarr")
    series_entries = [(target, series) for target in sonarr_targets for series in await target.sonarr().get_series()]

    await progress("file d'attente")
    movie_issues = await queue_issues(radarr_targets, "movieId")
    series_issues = await queue_issues(sonarr_targets, "seriesId")

    await progress("emby")
    emby_movies = await emby.get_library_items("Movie")
    emby_series = await emby.get_library_items("Series")
    ctx = library_context(emby, emby_movies, emby_series, movie_entries, series_entries, movie_issues, series_issues)
    results = await build_results(ctx, movie_entries, series_entries)

    # Médias de la bibliothèque non suivis par Sonarr/Radarr.
    await progress("bibliothèque")
    claimed = {r.media.emby_item_id for r in results if r.media.emby_item_id}
    results.extend(await build_untracked_results(ctx, emby_movies, emby_series, claimed))

    await progress("historique")
    hash_to_index = await _history_hashes(results, movie_entries, series_entries)

    await progress("qbittorrent")
    fetched = await fetch_torrents(settings)
    _attach(settings, results, fetched, hash_to_index)
    _apply_statuses(results)

    await progress("visionnage")
    emby_users = await _apply_watch_stats(settings, emby, results)
    await _apply_seer_requests(settings, results, progress)
    return results, fetched, emby_users


async def queue_issues(targets: list[ArrTarget], id_field: str) -> QueueIssues:
    """Imports bloqués et téléchargements en souffrance, indexés par
    (instance, id arr) : deux instances numérotent leurs médias
    indépendamment, un id seul rattacherait le problème au mauvais média."""
    issues: QueueIssues = {}
    for target in targets:
        client = target.radarr() if id_field == "movieId" else target.sonarr()
        try:
            records = await client.get_queue()
        except (httpx.HTTPError, ValueError):
            # Comme le visionnage : une file d'attente illisible ne doit jamais
            # faire échouer le scan, ses problèmes ne sont simplement pas signalés.
            logger.warning("File d'attente illisible sur %s", target.name, exc_info=True)
            continue
        for arr_id, found in index_queue_issues(records, id_field).items():
            issues.setdefault((target.instance_id, arr_id), []).extend(found)
    return issues


def library_context(
    emby: EmbyClient,
    emby_movies: list[dict[str, Any]],
    emby_series: list[dict[str, Any]],
    movie_entries: ArrEntries,
    series_entries: ArrEntries,
    movie_issues: QueueIssues,
    series_issues: QueueIssues,
) -> LibraryContext:
    """Index du serveur multimédia et fichiers suivis par chaque instance :
    un même film peut être suivi par plusieurs instances (ex : Radarr et
    Radarr 4K), et la version d'une autre instance n'est jamais un doublon."""
    movie_files_by_tmdb: dict[str, list[tuple[ArrTarget, dict[str, Any]]]] = {}
    for target, movie in movie_entries:
        if movie.get("hasFile") and movie.get("movieFile") and movie.get("tmdbId"):
            movie_files_by_tmdb.setdefault(str(movie["tmdbId"]), []).append((target, movie["movieFile"]))
    series_by_tvdb: dict[str, list[tuple[ArrTarget, int]]] = {}
    for target, series in series_entries:
        if (series.get("statistics") or {}).get("episodeFileCount") and series.get("tvdbId"):
            series_by_tvdb.setdefault(str(series["tvdbId"]), []).append((target, series["id"]))

    return LibraryContext(
        emby=emby,
        emby_movies_by_tmdb=_index_items(emby_movies, "Tmdb"),
        emby_movies_by_imdb=_index_items(emby_movies, "Imdb"),
        emby_series_by_tvdb=_index_items(emby_series, "Tvdb"),
        emby_series_by_imdb=_index_items(emby_series, "Imdb"),
        emby_series_by_tmdb=_index_items(emby_series, "Tmdb"),
        movie_files_by_tmdb=movie_files_by_tmdb,
        series_by_tvdb=series_by_tvdb,
        movie_issues=movie_issues,
        series_issues=series_issues,
        episode_files_for=_episode_files_loader(),
    )


def _episode_files_loader() -> Callable[[ArrTarget, int], Awaitable[list[dict[str, Any]]]]:
    """Fichiers d'épisodes d'une série, lus une seule fois par (instance, série)."""
    cache: dict[tuple[int | None, int], list[dict[str, Any]]] = {}

    async def load(target: ArrTarget, series_id: int) -> list[dict[str, Any]]:
        key = (target.instance_id, series_id)
        if key not in cache:
            try:
                cache[key] = await target.sonarr().get_episode_files(series_id)
            except (httpx.HTTPError, ValueError):
                # Sert seulement à repérer le fichier suivi (is_current) : le
                # scan continue sans, les fichiers restent non identifiés.
                logger.warning("Fichiers d'épisodes illisibles pour la série %s", series_id, exc_info=True)
                cache[key] = []
        return cache[key]

    return load


async def build_results(
    ctx: LibraryContext, movie_entries: ArrEntries, series_entries: ArrEntries
) -> list[MediaBuildResult]:
    """Construction déléguée à build_movie_result / build_series_result : le
    même code sert aux analyses par service (voir services/partial_scan.py)."""
    results: list[MediaBuildResult] = []
    for target, movie in movie_entries:
        movie_result = await build_movie_result(ctx, target, movie)
        if movie_result is not None:
            results.append(movie_result)
    for target, series in series_entries:
        series_result = await build_series_result(ctx, target, series)
        if series_result is not None:
            results.append(series_result)
    return results


async def _history_hashes(
    results: list[MediaBuildResult], movie_entries: ArrEntries, series_entries: ArrEntries
) -> dict[str, int]:
    """Correspondance torrent -> média via l'historique Sonarr/Radarr. Indexée
    par id Radarr/Sonarr et non par position (des films sans fichier n'ont pas
    de média), et par instance : les ids de deux instances se recouvrent."""
    radarr_id_to_index = {
        (r.media.arr_instance_id, r.media.radarr_id): i for i, r in enumerate(results) if r.media.radarr_id is not None
    }
    sonarr_id_to_index = {
        (r.media.arr_instance_id, r.media.sonarr_id): i for i, r in enumerate(results) if r.media.sonarr_id is not None
    }
    hash_to_index: dict[str, int] = {}
    for target, movie in movie_entries:
        index = radarr_id_to_index.get((target.instance_id, movie["id"])) if "id" in movie else None
        if index is not None:
            await _add_history(hash_to_index, index, target.radarr().get_history_for_movie, movie["id"])
    for target, series in series_entries:
        index = sonarr_id_to_index.get((target.instance_id, series["id"])) if "id" in series else None
        if index is not None:
            await _add_history(hash_to_index, index, target.sonarr().get_history_for_series, series["id"])
    return hash_to_index


async def _add_history(
    hash_to_index: dict[str, int],
    index: int,
    load: Callable[[int], Awaitable[list[dict[str, Any]]]],
    arr_id: int,
) -> None:
    try:
        history = await load(arr_id)
    except (httpx.HTTPError, ValueError):
        # Un historique illisible ne doit pas interrompre le scan : ses torrents
        # restent rattachables par inode, chemin ou nom.
        logger.warning("Historique Sonarr/Radarr illisible pour le média %s", arr_id, exc_info=True)
        return
    for event in history:
        download_id = event.get("downloadId")
        if download_id:
            hash_to_index[download_id.lower()] = index


def _attach(
    settings: Settings, results: list[MediaBuildResult], fetched: FetchedTorrents, hash_to_index: dict[str, int]
) -> None:
    """Rattachement délégué à services/torrent_match.py : le même code sert
    aux analyses partielles et à l'analyse d'un seul média, qui doivent
    rattacher exactement comme un scan complet."""
    views = [
        MediaView(
            media_type=r.media.media_type,
            title=r.media.title,
            year=r.media.year,
            alt_titles=r.alt_titles,
            root_path=r.root_path,
            files=r.files,
        )
        for r in results
    ]
    attached = attach_torrents(settings, views, fetched, hash_to_index)
    for result, torrents_of_media in zip(results, attached, strict=True):
        result.torrents.extend(torrents_of_media)


def _apply_statuses(results: list[MediaBuildResult]) -> None:
    for result in results:
        result.media.missing_emby_episodes = ",".join(result.missing_emby_episodes)
        # Mémorisés pour les analyses partielles, qui rattachent les torrents
        # sans redemander la liste à Sonarr/Radarr.
        result.media.root_path = result.root_path
        result.media.alt_titles = "\n".join(result.alt_titles)
        apply_statuses(result.media, result.files, result.torrents, result.import_issues)


async def _apply_watch_stats(settings: Settings, emby: EmbyClient, results: list[MediaBuildResult]) -> list[EmbyUser]:
    """Visionnage : purement informatif. Un échec (serveur trop ancien, droits
    insuffisants) laisse les statistiques vides sans faire échouer le scan."""
    try:
        emby_users = users_from_api(await emby.get_users())
        watch_data = await collect_watch_data(emby, emby_users)
    except (httpx.HTTPError, ValueError):
        logger.warning("Statistiques de visionnage illisibles", exc_info=True)
        emby_users, watch_data = [], {}
    excluded = excluded_user_ids(settings)
    for result in results:
        result.watches = build_watch_rows(result.media, emby_users, watch_data)
        apply_aggregates(result.media, result.watches, excluded)
    return emby_users


async def _apply_seer_requests(settings: Settings, results: list[MediaBuildResult], progress: Progress) -> None:
    """Demandes Seer (optionnel) : un Seer injoignable laisse les demandes
    vides sans faire échouer le scan."""
    seer = seer_client(settings)
    if seer is None:
        return
    await progress("seer")
    try:
        request_index = await fetch_request_index(seer)
    except (httpx.HTTPError, ValueError):
        logger.warning("Demandes du gestionnaire de demandes illisibles", exc_info=True)
        request_index = {}
    for result in results:
        result.requests = build_request_rows(result.media, request_index)
