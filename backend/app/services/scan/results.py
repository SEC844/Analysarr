"""Construction d'un média à partir de Sonarr/Radarr et du serveur multimédia. Partagée par le
scan complet et les analyses par service, pour qu'un média soit construit de la même façon."""

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.clients.emby import EmbyClient
from app.models.media import (
    ImportIssue,
    Media,
    MediaFile,
    MediaRequest,
    MediaType,
    MediaWatch,
    Torrent,
)
from app.services.arr_instances import ArrTarget
from app.services.queue_issues import issue_rows_for
from app.services.scan.library import (
    _common_root,
    _current_flags,
    _episode_label,
    _episode_span_labels,
    _library_file,
    _media_sources,
    _missing_emby_labels,
    _pick_emby_item,
    _pick_series_item,
    _provider_id,
    _to_int,
    _without_other_instance_files,
)
from app.services.watch_stats import (
    parse_emby_date,
)

logger = logging.getLogger(__name__)


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
    import_issues: list[ImportIssue] = field(default_factory=list)


@dataclass
class LibraryContext:
    """Tout ce qu'il faut pour construire un média : index du serveur
    multimédia, fichiers suivis par chaque instance, problèmes de file
    d'attente. Partagé par le scan complet et les analyses par service, pour
    qu'un média soit construit exactement de la même façon dans les deux cas."""

    emby: EmbyClient
    emby_movies_by_tmdb: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    emby_movies_by_imdb: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    emby_series_by_tvdb: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Séries indexées aussi par IMDb et TMDB : une série du serveur multimédia
    # sans identifiant TVDB passait pour absente de Sonarr, et se retrouvait
    # comptée deux fois (suivie ET non suivie).
    emby_series_by_imdb: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    emby_series_by_tmdb: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Fichiers suivis par CHAQUE instance : un fichier suivi par une autre
    # instance ne doit jamais être compté comme doublon.
    movie_files_by_tmdb: dict[str, list[tuple[ArrTarget, dict[str, Any]]]] = field(default_factory=dict)
    series_by_tvdb: dict[str, list[tuple[ArrTarget, int]]] = field(default_factory=dict)
    movie_issues: dict[tuple[int | None, int], list[dict[str, Any]]] = field(default_factory=dict)
    series_issues: dict[tuple[int | None, int], list[dict[str, Any]]] = field(default_factory=dict)
    # Fichiers d'épisodes d'une série, mis en cache par (instance, id série).
    episode_files_for: Any = None


def _alt_titles(title: str, candidates: list[str | None]) -> list[str]:
    """Titres alternatifs utiles au rattachement par nom : non vides et
    différents du titre principal."""
    return [t for t in candidates if t and t != title]


def apply_media_server_item(media: Media, item: dict[str, Any]) -> None:
    """Identité, jaquette et date d'ajout de l'item du serveur multimédia."""
    media.emby_item_id = item.get("Id")
    media.has_poster = bool(item.get("Id"))
    media.poster_image_tag = (item.get("ImageTags") or {}).get("Primary")
    media.emby_date_added = parse_emby_date(item.get("DateCreated"))


async def build_movie_result(ctx: LibraryContext, target: ArrTarget, movie: dict[str, Any]) -> MediaBuildResult | None:
    """Un film Radarr et ses fichiers. `None` s'il n'y a rien à analyser."""
    movie_id = movie.get("id")
    issues = ctx.movie_issues.get((target.instance_id, movie_id), []) if isinstance(movie_id, int) else []
    if not movie.get("hasFile") and not issues:
        # Pas encore téléchargé et rien de bloqué : rien à analyser. Un
        # import bloqué, lui, mérite d'apparaître même sans fichier —
        # c'est justement ce qui explique l'absence du média.
        return None
    result = _movie_result(target, movie)
    result.import_issues = issue_rows_for(issues)

    movie_file = movie.get("movieFile") or None
    tmdb_key = str(movie.get("tmdbId")) if movie.get("tmdbId") else None
    by_tmdb = ctx.emby_movies_by_tmdb.get(tmdb_key, []) if tmdb_key else []
    candidates = by_tmdb or ctx.emby_movies_by_imdb.get(movie.get("imdbId") or "", [])
    emby_item = _pick_emby_item(candidates, [movie_file] if movie_file else [])
    if emby_item:
        apply_media_server_item(result.media, emby_item)
        other_files = [
            f for other, f in ctx.movie_files_by_tmdb.get(tmdb_key or "", []) if other.instance_id != target.instance_id
        ]
        result.files = movie_files(emby_item, movie_file, other_files)
    return result


def _movie_result(target: ArrTarget, movie: dict[str, Any]) -> MediaBuildResult:
    """Média d'un film Radarr, sans ses fichiers."""
    media = Media(
        media_type=MediaType.movie,
        title=movie.get("title") or "Sans titre",
        year=movie.get("year"),
        radarr_id=movie.get("id"),
        arr_instance_id=target.instance_id,
        tmdb_id=movie.get("tmdbId"),
        imdb_id=movie.get("imdbId"),
    )
    alternate = [a.get("title") for a in movie.get("alternateTitles") or []]
    return MediaBuildResult(
        media=media,
        root_path=movie.get("path"),
        alt_titles=_alt_titles(media.title, [movie.get("originalTitle"), *alternate]),
    )


def movie_files(
    item: dict[str, Any], movie_file: dict[str, Any] | None, other_files: list[dict[str, Any]]
) -> list[MediaFile]:
    """Fichiers de l'item du film. Seul le fichier actuel correspond à un
    movieFile Radarr réel — les autres sont des doublons non suivis par Radarr,
    rien à supprimer côté Radarr."""
    sources = _without_other_instance_files(_media_sources(item), movie_file, other_files)
    flags = _current_flags([(s.get("Path"), s.get("Size")) for s in sources], movie_file)
    current_id = (movie_file or {}).get("id")
    return [
        _library_file(source, None, is_current=is_current, arr_file_id=current_id if is_current else None)
        for source, is_current in zip(sources, flags, strict=True)
    ]


async def build_series_result(
    ctx: LibraryContext, target: ArrTarget, series: dict[str, Any]
) -> MediaBuildResult | None:
    """Une série Sonarr et ses fichiers. `None` s'il n'y a rien à analyser."""
    series_id = series.get("id")
    issues = ctx.series_issues.get((target.instance_id, series_id), []) if isinstance(series_id, int) else []
    if not (series.get("statistics") or {}).get("episodeFileCount") and not issues:
        return None  # aucun épisode téléchargé ni import bloqué : rien à analyser
    media = Media(
        media_type=MediaType.series,
        title=series.get("title") or "Sans titre",
        year=series.get("year"),
        sonarr_id=series.get("id"),
        arr_instance_id=target.instance_id,
        tvdb_id=series.get("tvdbId"),
    )
    result = MediaBuildResult(
        media=media,
        root_path=series.get("path"),
        alt_titles=_alt_titles(media.title, [a.get("title") for a in series.get("alternateTitles") or []]),
    )
    result.import_issues = issue_rows_for(issues)

    tvdb_key = str(series.get("tvdbId")) if series.get("tvdbId") else None
    candidates = (ctx.emby_series_by_tvdb.get(tvdb_key, []) if tvdb_key else []) or _series_fallback(ctx, series)
    if candidates:
        await _fill_series_files(ctx, target, series, tvdb_key, candidates, result)
    return result


async def _fill_series_files(
    ctx: LibraryContext,
    target: ArrTarget,
    series: dict[str, Any],
    tvdb_key: str | None,
    candidates: list[dict[str, Any]],
    result: MediaBuildResult,
) -> None:
    """Fichiers d'une série présente sur le serveur multimédia, et épisodes
    téléchargés par Sonarr que le serveur n'a pas repris."""
    episode_files = await ctx.episode_files_for(target, series["id"])
    other_files = [
        f
        for other, other_series_id in ctx.series_by_tvdb.get(tvdb_key or "", [])
        if other.instance_id != target.instance_id
        for f in await ctx.episode_files_for(other, other_series_id)
    ]

    emby_item, episodes = await _pick_series_item(ctx.emby, candidates, episode_files)
    apply_media_server_item(result.media, emby_item)
    downloaded, sonarr_by_label = await sonarr_episodes(target, series["id"])

    result.media.episode_count = len(episodes)
    sources_by_label, span_by_label = episode_sources(episodes)
    result.files = series_files(sources_by_label, sonarr_by_label, episode_files, other_files)

    # La série ELLE-MÊME est bien sur le serveur multimédia, mais certains
    # épisodes téléchargés par Sonarr peuvent y manquer (import manqué) sans
    # que ça ne se voie autrement : aucun fichier n'est créé pour eux, puisque
    # seuls les épisodes renvoyés par le serveur sont parcourus.
    result.missing_emby_episodes = _missing_emby_labels(
        downloaded, [f.episode_label for f in result.files], span_by_label
    )


async def sonarr_episodes(target: ArrTarget, series_id: int) -> tuple[set[str], dict[str, tuple[int, int | None]]]:
    """Épisodes que Sonarr considère téléchargés (episodeFile existant),
    indépendamment de ce que le serveur multimédia en a repris, et identité
    Sonarr de chaque épisode : son id pour le monitoring, son episodeFileId
    pour la suppression (voir routers/media.py, delete-selection)."""
    downloaded: set[str] = set()
    by_label: dict[str, tuple[int, int | None]] = {}
    try:
        for e in await target.sonarr().get_episodes(series_id):
            if e.get("seasonNumber") is None or e.get("episodeNumber") is None:
                continue
            label = f"S{e['seasonNumber']:02d}E{e['episodeNumber']:02d}"
            if e.get("hasFile"):
                downloaded.add(label)
            by_label[label] = (e["id"], e.get("episodeFileId"))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        # Purement informatif : on garde ce qui a été lu. Sans cette liste,
        # les épisodes absents du serveur multimédia ne sont pas signalés et
        # les fichiers n'ont pas d'identité Sonarr — le scan continue.
        logger.warning("Épisodes Sonarr illisibles pour la série %s", series_id, exc_info=True)
    return downloaded, by_label


def episode_sources(
    episodes: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, set[str]]]:
    """Fichiers regroupés par épisode — un doublon peut être un item distinct
    du même épisode, pas seulement une seconde source — et épisodes couverts
    par chaque fichier, pour ne pas croire absent le deuxième épisode d'un
    fichier multi-épisodes."""
    sources_by_label: dict[str, list[dict[str, Any]]] = {}
    span_by_label: dict[str, set[str]] = {}
    for episode in episodes:
        label = _episode_label(episode)
        sources_by_label.setdefault(label, []).extend(_media_sources(episode))
        span_by_label.setdefault(label, set()).update(_episode_span_labels(episode))
    return sources_by_label, span_by_label


def series_files(
    sources_by_label: dict[str, list[dict[str, Any]]],
    sonarr_by_label: dict[str, tuple[int, int | None]],
    episode_files: list[dict[str, Any]],
    other_files: list[dict[str, Any]],
) -> list[MediaFile]:
    """Fichiers de la série, épisode par épisode. Comme pour les films, seul le
    fichier actuel correspond à l'episodeFile Sonarr réel."""
    current_paths = {f["path"] for f in episode_files if f.get("path")}
    episode_files_by_id = {f["id"]: f for f in episode_files if f.get("id")}
    files: list[MediaFile] = []
    for label, label_sources in sources_by_label.items():
        episode_id, episode_file_id = sonarr_by_label.get(label, (None, None))
        episode_file = episode_files_by_id.get(episode_file_id) if episode_file_id else None
        sources = _without_other_instance_files(label_sources, episode_file, other_files)
        if episode_file is not None:
            flags = _current_flags([(s.get("Path"), s.get("Size")) for s in sources], episode_file)
        else:
            flags = [bool(s.get("Path") and s.get("Path") in current_paths) for s in sources]
        files += [
            _library_file(
                source,
                label,
                is_current=is_current,
                sonarr_episode_id=episode_id if is_current else None,
                arr_file_id=episode_file_id if is_current else None,
            )
            for source, is_current in zip(sources, flags, strict=True)
        ]
    return files


def _series_fallback(ctx: LibraryContext, series: dict[str, Any]) -> list[dict[str, Any]]:
    """Repli quand l'identifiant TVDB ne donne rien : IMDb puis TMDB."""
    imdb = series.get("imdbId")
    tmdb = series.get("tmdbId")
    return (ctx.emby_series_by_imdb.get(str(imdb), []) if imdb else []) or (
        ctx.emby_series_by_tmdb.get(str(tmdb), []) if tmdb else []
    )


def _apply_provider_ids(media: Media, item: dict[str, Any]) -> None:
    """Identifiants externes du serveur multimédia : ce sont eux qui permettent
    d'ajouter ensuite le média dans Radarr ou Sonarr."""
    provider_ids = item.get("ProviderIds")
    media.tmdb_id = _to_int(_provider_id(provider_ids, "Tmdb"))
    media.tvdb_id = _to_int(_provider_id(provider_ids, "Tvdb"))
    media.imdb_id = _provider_id(provider_ids, "Imdb")


def _library_media(item: dict[str, Any], media_type: MediaType) -> Media:
    media = Media(
        media_type=media_type,
        title=item.get("Name") or "Sans titre",
        year=item.get("ProductionYear"),
        emby_item_id=item.get("Id"),
        has_poster=bool(item.get("Id")),
        poster_image_tag=(item.get("ImageTags") or {}).get("Primary"),
        emby_date_added=parse_emby_date(item.get("DateCreated")),
    )
    _apply_provider_ids(media, item)
    return media


def build_untracked_movie(item: dict[str, Any]) -> MediaBuildResult | None:
    """Film présent sur le serveur multimédia mais suivi par aucun Radarr."""
    sources = _media_sources(item)
    if not sources:
        return None
    media = _library_media(item, MediaType.movie)
    files = [_library_file(source, None) for source in sources]
    return MediaBuildResult(media=media, root_path=_common_root([f.path for f in files]), files=files)


async def build_untracked_series(ctx: LibraryContext, item: dict[str, Any]) -> MediaBuildResult | None:
    """Série présente sur le serveur multimédia mais suivie par aucun Sonarr."""
    episodes = await ctx.emby.get_episodes(item["Id"])
    files = [
        _library_file(source, _episode_label(episode)) for episode in episodes for source in _media_sources(episode)
    ]
    if not files:
        return None
    media = _library_media(item, MediaType.series)
    media.episode_count = len(episodes)
    return MediaBuildResult(media=media, root_path=_common_root([f.path for f in files]), files=files)


async def build_untracked_results(
    ctx: LibraryContext,
    emby_movies: list[dict[str, Any]],
    emby_series: list[dict[str, Any]],
    claimed_item_ids: set[str],
) -> list[MediaBuildResult]:
    """Médias de la bibliothèque qu'aucun Radarr/Sonarr ne suit.

    Sans eux, Analysarr ne voyait qu'une moitié du problème : un film ajouté à
    la main, une série retirée de Sonarr ou une bibliothèque montée avant
    l'installation de Radarr restaient totalement invisibles, avec leurs
    doublons et leurs torrents orphelins."""
    results: list[MediaBuildResult] = []
    for item in emby_movies:
        if item.get("Id") in claimed_item_ids:
            continue
        built = build_untracked_movie(item)
        if built is not None:
            results.append(built)
    for item in emby_series:
        if item.get("Id") in claimed_item_ids:
            continue
        built = await build_untracked_series(ctx, item)
        if built is not None:
            results.append(built)
    return results
