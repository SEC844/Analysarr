"""Analyse d'un seul média, depuis sa fiche.

Même résultat qu'un scan complet pour CE média — nouveaux torrents détectés,
fichiers disparus retirés, statuts recalculés — mais sans relire toute la
bibliothèque, et sans changer l'identifiant de la fiche (la page reste
valide après l'analyse).

Garde-fous :

- un torrent déjà rattaché à un AUTRE média n'est jamais volé ; seuls sont
  réexaminés les torrents de ce média et ceux qui ne sont rattachés à rien,
  y compris ceux que le chemin racine ou le nom désignent (voir
  `_candidate_positions`) ;
- le détail (fichiers, trackers) n'est demandé au client torrent que pour ces
  candidats : c'est ce qui rend l'analyse d'un média rapide ;
- si Sonarr/Radarr ne suit plus le média, sa fiche est supprimée plutôt que
  laissée avec des données mortes ;
- les demandes Seer ne sont pas relues (l'API ne permet pas de filtrer par
  média) : elles restent celles du dernier scan complet.
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlmodel import Session, col, delete, select

from app.clients.emby import EmbyClient, media_server_client
from app.clients.torrent import torrent_client, torrent_client_configured
from app.models.ids import row_id
from app.models.media import ImportIssue, Media, MediaFile, MediaRequest, MediaType, MediaWatch, Torrent
from app.models.settings import Settings
from app.services.arr_instances import arr_target_for, arr_targets
from app.services.queue_issues import index_queue_issues, issue_rows_for
from app.services.scan import (
    LibraryContext,
    _missing_emby_labels,
    _pick_emby_item,
    _pick_series_item,
    build_untracked_movie,
    build_untracked_series,
)
from app.services.scan.results import (
    apply_media_server_item,
    episode_sources,
    movie_files,
    series_files,
    sonarr_episodes,
)
from app.services.scan.statuses import apply_statuses
from app.services.torrent_match import (
    FetchedTorrents,
    MediaView,
    add_fetched,
    attach_torrents,
    optional_read,
)
from app.services.watch_stats import refresh_media_watch

logger = logging.getLogger(__name__)

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


def _media_server(settings: Settings) -> EmbyClient:
    """Serveur multimédia configuré : l'analyse d'un média n'a pas de sens sans
    lui. RuntimeError, et non une AttributeError sur None : la route la traduit
    en message lisible au lieu d'une erreur 500 (bug réel)."""
    emby = media_server_client(settings)
    if emby is None:
        raise RuntimeError("Serveur multimédia non configuré.")
    return emby


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

    apply_statuses(media, files, torrents, issues)
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
        statuses=_statuses(media),
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
            # Instance injoignable : les autres peuvent encore suivre le média.
            logger.warning("%s injoignable pour retrouver le média %s", target.name, media.id, exc_info=True)
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
    # Un rattachement a pu avoir lieu entre-temps : dans ce cas le média
    # redevient un média suivi, et c'est le chemin normal qui s'applique.
    if await _adopt_arr_entry(session, settings, media, is_series):
        return await rescan_media(session, settings, media)

    emby = _media_server(settings)
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

    apply_media_server_item(media, item)
    media.title = built.media.title
    media.year = built.media.year
    media.episode_count = built.media.episode_count
    media.root_path = built.root_path
    media.tmdb_id, media.tvdb_id, media.imdb_id = built.media.tmdb_id, built.media.tvdb_id, built.media.imdb_id

    session.exec(delete(MediaFile).where(col(MediaFile.media_id) == media.id))
    files: list[MediaFile] = []
    for row in built.files:
        row.media_id = row_id(media)
        session.add(row)
        files.append(row)
    session.commit()

    torrents = await _rebuild_torrents(session, settings, media, files)
    # Média non suivi : aucune file d'attente Sonarr/Radarr ne le concerne.
    apply_statuses(media, files, torrents, [])
    session.add(media)
    session.commit()

    await refresh_media_watch(session, media, settings)
    session.commit()
    return MediaRescanResult(files=len(files), torrents=len(torrents), statuses=_statuses(media))


def _statuses(media: Media) -> list[str]:
    return [status for status in media.statuses.split(",") if status]


def _delete_media(session: Session, media: Media) -> None:
    session.exec(delete(ImportIssue).where(col(ImportIssue.media_id) == media.id))
    session.exec(delete(MediaRequest).where(col(MediaRequest.media_id) == media.id))
    session.exec(delete(MediaWatch).where(col(MediaWatch.media_id) == media.id))
    session.exec(delete(Torrent).where(col(Torrent.media_id) == media.id))
    session.exec(delete(MediaFile).where(col(MediaFile.media_id) == media.id))
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
    client = target.sonarr() if is_series else target.radarr()
    try:
        queue = await client.get_queue()
    except (httpx.HTTPError, ValueError):
        # Informatif : sans file d'attente, les imports bloqués ne sont
        # simplement pas signalés.
        logger.warning("File d'attente illisible pour le média %s", media.id, exc_info=True)
    else:
        records = index_queue_issues(queue, "seriesId" if is_series else "movieId").get(arr_id, [])

    session.exec(delete(ImportIssue).where(col(ImportIssue.media_id) == media.id))
    rows = issue_rows_for(records)
    for row in rows:
        row.media_id = row_id(media)
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
    multimédia et de Sonarr/Radarr, avec le même code que le scan complet."""
    emby = _media_server(settings)
    rows = await (_series_rows(emby, media, target, entry) if is_series else _movie_rows(emby, media, entry))
    session.exec(delete(MediaFile).where(col(MediaFile.media_id) == media.id))
    for row in rows:
        row.media_id = row_id(media)
        session.add(row)
    session.commit()
    return rows


async def _series_rows(emby: EmbyClient, media: Media, target: Any, entry: dict[str, Any]) -> list[MediaFile]:
    episode_files = await target.sonarr().get_episode_files(entry["id"])
    item, episodes = await _series_item(emby, media, episode_files)
    if item is None:
        # Série disparue du serveur multimédia : la fiche ne garde pas un
        # lien mort, le statut « absent » prend le relais.
        _clear_media_server_item(media)
        media.missing_emby_episodes = ""
        return []
    apply_media_server_item(media, item)
    media.episode_count = len(episodes)
    downloaded, sonarr_by_label = await sonarr_episodes(target, entry["id"])
    sources_by_label, spans = episode_sources(episodes)
    rows = series_files(sources_by_label, sonarr_by_label, episode_files, [])
    media.missing_emby_episodes = ",".join(_missing_emby_labels(downloaded, [r.episode_label for r in rows], spans))
    return rows


async def _movie_rows(emby: EmbyClient, media: Media, entry: dict[str, Any]) -> list[MediaFile]:
    movie_file = entry.get("movieFile") or None
    item = await _movie_item(emby, media, movie_file)
    if item is None:
        _clear_media_server_item(media)
        return []
    apply_media_server_item(media, item)
    return movie_files(item, movie_file, [])


def _clear_media_server_item(media: Media) -> None:
    media.emby_item_id = None
    media.has_poster = False
    media.poster_image_tag = None


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


def _candidate_positions(media: Media, listed: list[dict[str, Any]], known_hashes: dict[str, int | None]) -> list[int]:
    """Torrents à réexaminer pour CE média : les siens, et ceux qui ne sont
    rattachés à aucun média (tout juste ajoutés, ou désignés par le chemin
    racine ou le titre du média). Les torrents d'un autre média sont laissés
    tranquilles — seul un scan complet peut les déplacer."""
    return [
        pos
        for pos, raw in enumerate(listed)
        if known_hashes.get(str(raw.get("hash") or "").lower()) in (None, media.id)
    ]


async def _rebuild_torrents(
    session: Session, settings: Settings, media: Media, files: list[MediaFile]
) -> list[Torrent]:
    """Rattache les torrents candidats à ce média. Le détail (fichiers,
    trackers) n'est demandé que pour eux."""
    if not torrent_client_configured(settings):
        session.exec(delete(Torrent).where(col(Torrent.media_id) == media.id))
        session.commit()
        return []

    known_hashes = {t.hash.lower(): t.media_id for t in session.exec(select(Torrent)).all() if t.hash}

    async with torrent_client(settings) as client:
        listed = await client.get_torrents()
        positions = _candidate_positions(media, listed, known_hashes)
        fetched = FetchedTorrents()
        for pos in positions:
            raw = listed[pos]
            trackers = await optional_read(client.get_trackers, raw["hash"], "Trackers")
            files_raw = await optional_read(client.get_files, raw["hash"], "Fichiers")
            # Même ligne Torrent que le scan complet (torrent_match.fetch_torrents).
            add_fetched(fetched, raw, trackers, files_raw)

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

    session.exec(delete(Torrent).where(col(Torrent.media_id) == media.id))
    for torrent in attached:
        torrent.media_id = row_id(media)
        session.add(torrent)
    session.commit()
    return attached
