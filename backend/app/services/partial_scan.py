"""Analyses partielles : rafraîchir UNE source de données sans reconstruire
toute la bibliothèque.

Principe non négociable : chaque périmètre laisse la base dans un état
cohérent. Une analyse partielle ne met à jour que ce qu'elle a réellement
relu, puis recalcule les statuts des médias concernés à partir du cache pour
le reste. Aucun statut ne peut donc rester calculé sur des données à moitié
rafraîchies.

Périmètres gérés ici :

- `radarr`, `sonarr` : relisent la liste, les fichiers suivis et la file
  d'attente de leur service ; seuls à pouvoir ajouter ou retirer un média,
  puisque la liste des médias vient d'eux ;
- `media_server` : relit les fichiers de bibliothèque des films et des séries ;
- `torrents` : relit le client torrent, rattache et recalcule les statuts ;
- `queue`    : relit la file d'attente Sonarr/Radarr (imports bloqués) ;
- `watch`    : relit les statistiques de visionnage ;
- `seer`     : relit les demandes Seer.

Les médias sont reconstruits avec le MÊME code que le scan complet
(`services/scan/`), et les lignes sont mises à jour en place : les
identifiants de fiche restent valides. Seul `full` passe par le scan complet.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlmodel import Session, col, delete, select

from app.clients.emby import media_server_client, media_server_name
from app.clients.torrent import torrent_client_configured, torrent_client_name
from app.database import engine
from app.models.ids import row_id
from app.models.media import (
    EmbyUser,
    ImportIssue,
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
from app.services.arr_instances import arr_targets
from app.services.events import scan_events
from app.services.notifications import ChannelTarget, channel_targets
from app.services.queue_issues import issue_rows_for

# Sous-modules du package de scan, jamais le package lui-même : il importe ce
# module (dans run_scan) et n'est peut-être pas encore entièrement chargé.
from app.services.scan.collect import build_results, library_context, queue_issues
from app.services.scan.orchestrator import _fail_scan
from app.services.scan.results import build_untracked_results
from app.services.scan.statuses import compute_statuses, current_files_size, is_tracked_by_arr
from app.services.scan_scopes import SERVICE_SCOPES
from app.services.seer import (
    build_request_rows,
    fetch_request_index,
    request_manager_name,
    seer_client,
    seer_configured,
)
from app.services.torrent_match import (
    MediaView,
    attach_torrents,
    fetch_torrents,
    persist_files,
    torrents_from_cache,
)
from app.services.watch_stats import (
    apply_aggregates,
    build_watch_rows,
    collect_watch_data,
    excluded_user_ids,
    users_from_api,
)

logger = logging.getLogger(__name__)

__all__ = ["run_service_scan"]


def _media_index(medias: list[Media]) -> dict[int, int]:
    return {media.id: i for i, media in enumerate(medias) if media.id is not None}


def _recompute_statuses(session: Session, medias: list[Media]) -> None:
    """Recalcule statuts, espace récupérable et poids de chaque média à partir
    de ce que la base contient MAINTENANT. Appelé à la fin de chaque analyse
    partielle : les statuts croisent plusieurs sources, ils ne peuvent pas
    rester figés parce qu'une seule a été relue."""
    for media in medias:
        files = list(session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all())
        torrents = list(session.exec(select(Torrent).where(Torrent.media_id == media.id)).all())
        issues = list(session.exec(select(ImportIssue).where(ImportIssue.media_id == media.id)).all())
        statuses, reclaimable = compute_statuses(
            files,
            torrents,
            bool(media.emby_item_id),
            len([label for label in media.missing_emby_episodes.split(",") if label]),
            {i.download_id.lower() for i in issues if i.download_id},
            {i.kind for i in issues},
            tracked_by_arr=is_tracked_by_arr(media),
        )
        media.statuses = ",".join(sorted(statuses))
        media.reclaimable_bytes = reclaimable
        media.total_size = current_files_size(files)
        session.add(media)


def _views(session: Session, medias: list[Media]) -> list[MediaView]:
    return [
        MediaView(
            media_type=media.media_type,
            title=media.title,
            year=media.year,
            alt_titles=[t for t in (media.alt_titles or "").split("\n") if t],
            root_path=media.root_path,
            files=list(session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()),
        )
        for media in medias
    ]


async def _refresh_torrents(session: Session, settings: Settings, medias: list[Media]) -> tuple[int, int]:
    """Relit tous les torrents et les rattache. L'historique Sonarr/Radarr
    n'est pas redemandé : le rattachement déjà établi pour un hash connu est
    réutilisé, ce qui donne le même résultat qu'un scan complet sans un appel
    par média."""
    index_by_media_id = _media_index(medias)
    known = list(session.exec(select(Torrent)).all())
    hash_to_index = {
        t.hash.lower(): index_by_media_id[t.media_id] for t in known if t.media_id in index_by_media_id
    }

    fetched = await fetch_torrents(settings)
    by_media = attach_torrents(settings, _views(session, medias), fetched, hash_to_index)

    session.exec(delete(Torrent))
    session.commit()
    matched = 0
    for media, torrents in zip(medias, by_media, strict=True):
        for torrent in torrents:
            torrent.media_id = row_id(media)
            session.add(torrent)
            matched += 1
    session.commit()
    # Fichiers mémorisés : les analyses de bibliothèque recalculeront les
    # hardlinks sans rappeler le client torrent.
    persist_files(session, fetched)
    return len(fetched), matched


async def _refresh_queue(session: Session, settings: Settings, medias: list[Media]) -> None:
    """Relit la file d'attente de chaque instance Sonarr/Radarr, avec le même
    code que le scan complet (une instance illisible est ignorée)."""
    issues_by_key: dict[tuple[str, int | None, int], list[dict[str, Any]]] = {}
    for kind, id_field in (("radarr", "movieId"), ("sonarr", "seriesId")):
        found = await queue_issues(arr_targets(session, settings, kind), id_field)
        for (instance_id, arr_id), records in found.items():
            issues_by_key[(kind, instance_id, arr_id)] = records

    session.exec(delete(ImportIssue))
    session.commit()
    for media in medias:
        kind = "radarr" if media.media_type == MediaType.movie else "sonarr"
        media_arr_id = media.radarr_id if kind == "radarr" else media.sonarr_id
        if media_arr_id is None:
            continue
        records = issues_by_key.get((kind, media.arr_instance_id, media_arr_id), [])
        for row in issue_rows_for(records):
            row.media_id = row_id(media)
            session.add(row)
    session.commit()


async def _refresh_watch(session: Session, settings: Settings, medias: list[Media]) -> None:
    """Relit les statistiques de visionnage. Échec silencieux : des stats
    vides valent mieux qu'une analyse en erreur."""
    emby = media_server_client(settings)
    if emby is None:
        return
    try:
        users = users_from_api(await emby.get_users())
        data = await collect_watch_data(emby, users)
    except (httpx.HTTPError, ValueError):
        # Les statistiques du dernier scan restent en place.
        logger.warning("Statistiques de visionnage illisibles", exc_info=True)
        return

    session.exec(delete(MediaWatch))
    session.exec(delete(EmbyUser))
    session.commit()
    for user in users:
        session.add(user)
    excluded = excluded_user_ids(settings)
    for media in medias:
        rows = build_watch_rows(media, users, data)
        for row in rows:
            row.media_id = row_id(media)
            session.add(row)
        apply_aggregates(media, rows, excluded)
        session.add(media)
    session.commit()


async def _refresh_seer(session: Session, settings: Settings, medias: list[Media]) -> None:
    """Relit les demandes Seer. Échec silencieux, comme pendant un scan."""
    seer = seer_client(settings)
    if seer is None:
        return
    try:
        index = await fetch_request_index(seer)
    except (httpx.HTTPError, ValueError):
        # Les demandes du dernier scan restent en place.
        logger.warning("Demandes du gestionnaire de demandes illisibles", exc_info=True)
        return

    session.exec(delete(MediaRequest))
    session.commit()
    for media in medias:
        rows = build_request_rows(media, index)
        for row in rows:
            row.media_id = row_id(media)
            session.add(row)
        session.add(media)
    session.commit()


def _missing_service(scope: str, settings: Settings) -> str | None:
    """Service indispensable au périmètre demandé, s'il n'est pas configuré."""
    if scope == "torrents" and not torrent_client_configured(settings):
        return torrent_client_name(settings)
    if scope in ("watch", "media_server", "radarr", "sonarr") and not (settings.emby_url and settings.emby_api_key):
        # Les trois analyses de bibliothèque relisent aussi le serveur
        # multimédia : c'est lui qui porte les fichiers.
        return media_server_name(settings)
    if scope == "radarr" and not (settings.radarr_url and settings.radarr_api_key):
        return "Radarr"
    if scope == "sonarr" and not (settings.sonarr_url and settings.sonarr_api_key):
        return "Sonarr"
    if scope == "media_server" and not (settings.radarr_url and settings.sonarr_url):
        return "Sonarr/Radarr"
    if scope == "seer" and not seer_configured(settings):
        return request_manager_name(settings)
    return None


async def run_service_scan(scope: str, trigger: str = "manual") -> None:
    """Analyse d'un seul service. L'appelant détient déjà le verrou de scan
    (voir `services/scan/__init__.py::run_scan`) : une analyse par service et un scan
    complet ne tournent jamais en même temps."""
    if scope not in SERVICE_SCOPES:
        raise ValueError(f"Périmètre d'analyse inconnu : {scope}")

    with Session(engine) as session:
        settings = session.get(Settings, 1)
        if settings is not None:
            session.expunge(settings)
        channels: list[ChannelTarget] = channel_targets(session)
        run = ScanRun(status=ScanStatus.running, trigger=trigger, scope=scope)
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = row_id(run)

    await scan_events.publish({"type": "started", "run_id": run_id, "scope": scope})

    if settings is None:
        await _fail_scan(run_id, "Aucune configuration enregistrée.", channels, settings)
        return
    missing = _missing_service(scope, settings)
    if missing:
        await _fail_scan(run_id, f"Services non configurés : {missing}.", channels, settings)
        return

    torrent_count = matched_count = 0
    try:
        await scan_events.publish({"type": "progress", "run_id": run_id, "stage": scope})
        with Session(engine) as session:
            if scope in ("radarr", "sonarr", "media_server"):
                # Reconstruit les médias du service : lui seul peut en ajouter
                # ou en retirer, et les statuts sont recalculés derrière.
                torrent_count, matched_count = await _refresh_service(session, settings, scope)
                medias = list(session.exec(select(Media)).all())
            else:
                medias = list(session.exec(select(Media)).all())
                if scope == "torrents":
                    torrent_count, matched_count = await _refresh_torrents(session, settings, medias)
                elif scope == "queue":
                    await _refresh_queue(session, settings, medias)
                elif scope == "watch":
                    await _refresh_watch(session, settings, medias)
                else:
                    await _refresh_seer(session, settings, medias)

                # Visionnage et Seer ne changent aucun statut : inutile de tout
                # recalculer pour rien.
                if scope in ("torrents", "queue"):
                    await scan_events.publish({"type": "progress", "run_id": run_id, "stage": "statuts"})
                    _recompute_statuses(session, medias)
            session.commit()

            stored = session.get(ScanRun, run_id)
            if stored is None:
                raise RuntimeError(f"Analyse {run_id} introuvable en base")
            run = stored
            run.status = ScanStatus.completed
            run.finished_at = datetime.now(UTC)
            run.media_count = len(medias)
            run.duplicate_count = sum(1 for m in medias if "doublon" in m.statuses.split(","))
            run.orphan_count = sum(1 for m in medias if "orphelin_qbit" in m.statuses.split(","))
            run.non_hardlink_count = sum(1 for m in medias if "non_hardlink" in m.statuses.split(","))
            run.tracker_unique_count = sum(1 for m in medias if "tracker_unique" in m.statuses.split(","))
            run.qbittorrent_torrent_count = torrent_count
            run.qbittorrent_matched_count = matched_count
            session.add(run)
            session.commit()
            summary = {
                "media_count": run.media_count,
                "duplicate_count": run.duplicate_count,
                "orphan_count": run.orphan_count,
            }
    except Exception as exc:  # noqa: BLE001 - toute erreur externe est reportée, jamais propagée
        await _fail_scan(run_id, f"{type(exc).__name__} : {exc}", channels, settings)
        return

    await scan_events.publish({"type": "completed", "run_id": run_id, "scope": scope, **summary})


# --- Analyses par service (Sonarr, Radarr, serveur multimédia) ---------------
# Elles reconstruisent les médias du service concerné avec le MÊME code que le
# scan complet (`build_movie_result` / `build_series_result`), puis réutilisent
# les torrents déjà connus : leurs fichiers sont mémorisés en base, donc l'état
# hardlinké et réparable est recalculé en relisant les inodes sur le disque,
# sans redemander au client torrent ses fichiers un par un. C'est ce qui rend
# ces analyses bien plus rapides qu'un scan complet, qui interroge en plus
# l'historique de chaque média et tout le client torrent.


def _arr_key(media: Media) -> tuple[str, Any, Any]:
    """Identité d'un média d'une analyse à l'autre. Un média non suivi par
    Sonarr/Radarr n'a pas d'identifiant arr : c'est son item du serveur
    multimédia qui l'identifie, sinon tous ces médias partageraient la même
    clé et s'écraseraient entre eux."""
    arr_id = media.radarr_id if media.media_type == MediaType.movie else media.sonarr_id
    if arr_id is None:
        return (media.media_type.value, "library", media.emby_item_id)
    return (media.media_type.value, media.arr_instance_id, arr_id)


def _copy_media_fields(row: Media, source: Media) -> None:
    """Recopie ce que le service vient de relire en gardant l'identifiant de la
    fiche (liens et pages ouvertes restent valides), ainsi que le visionnage et
    le demandeur Seer, qui viennent d'autres sources."""
    for name in (
        "title",
        "year",
        "root_path",
        "alt_titles",
        "tmdb_id",
        "tvdb_id",
        "imdb_id",
        "emby_item_id",
        "has_poster",
        "poster_image_tag",
        "emby_date_added",
        "episode_count",
        "missing_emby_episodes",
    ):
        setattr(row, name, getattr(source, name))


async def _refresh_service(session: Session, settings: Settings, scope: str) -> tuple[int, int]:
    """Relit un service et reconstruit les médias qu'il porte, avec le MÊME
    code que le scan complet (services/scan/collect.py).

    - `radarr` : les films, leur file d'attente et leurs fichiers ;
    - `sonarr` : les séries, idem ;
    - `media_server` : les fichiers de bibliothèque des films ET des séries.
    """
    wants_movies = scope in ("radarr", "media_server")
    wants_series = scope in ("sonarr", "media_server")
    emby = media_server_client(settings)
    if emby is None:
        raise RuntimeError("Serveur multimédia non configuré.")

    radarr_targets = arr_targets(session, settings, "radarr") if wants_movies else []
    sonarr_targets = arr_targets(session, settings, "sonarr") if wants_series else []
    movie_entries = [(target, movie) for target in radarr_targets for movie in await target.radarr().get_movies()]
    series_entries = [(target, entry) for target in sonarr_targets for entry in await target.sonarr().get_series()]
    emby_movies = await emby.get_library_items("Movie") if wants_movies else []
    emby_series = await emby.get_library_items("Series") if wants_series else []
    # File d'attente : relue avec le service qui la porte, conservée sinon.
    movie_issues = await queue_issues(radarr_targets, "movieId") if scope == "radarr" else {}
    series_issues = await queue_issues(sonarr_targets, "seriesId") if scope == "sonarr" else {}

    ctx = library_context(emby, emby_movies, emby_series, movie_entries, series_entries, movie_issues, series_issues)
    results = await build_results(ctx, movie_entries, series_entries)

    # Les médias non suivis par Sonarr/Radarr viennent de la bibliothèque : seule
    # une analyse du serveur multimédia peut les reconstruire, et elle seule a le
    # droit de retirer ceux qui n'y sont plus.
    untracked = scope == "media_server"
    if untracked:
        claimed = {r.media.emby_item_id for r in results if r.media.emby_item_id}
        results.extend(await build_untracked_results(ctx, emby_movies, emby_series, claimed))

    return _persist_service_results(
        session,
        settings,
        results,
        wants_movies,
        wants_series,
        refresh_queue=scope != "media_server",
        prune_untracked=untracked,
    )


def _persist_service_results(
    session: Session,
    settings: Settings,
    results: list[Any],
    wants_movies: bool,
    wants_series: bool,
    refresh_queue: bool,
    prune_untracked: bool = False,
) -> tuple[int, int]:
    """Écrit les médias reconstruits SANS changer leurs identifiants, puis
    rattache les torrents connus et recalcule tous les statuts."""
    existing = {_arr_key(media): media for media in session.exec(select(Media)).all()}
    seen: set[tuple[str, int | None, int | None]] = set()

    for result in results:
        key = _arr_key(result.media)
        seen.add(key)
        row = existing.get(key)
        if row is None:
            session.add(result.media)
            session.commit()
            session.refresh(result.media)
            row = result.media
        else:
            _copy_media_fields(row, result.media)
            session.add(row)
            session.commit()

        session.exec(delete(MediaFile).where(col(MediaFile.media_id) == row.id))
        for f in result.files:
            f.media_id = row_id(row)
            session.add(f)
        if refresh_queue:
            session.exec(delete(ImportIssue).where(col(ImportIssue.media_id) == row.id))
            for issue in result.import_issues:
                issue.media_id = row_id(row)
                session.add(issue)
        session.commit()

    # Média du périmètre que le service ne suit plus : sa fiche disparaît.
    for key, row in existing.items():
        is_movie = key[0] == MediaType.movie.value
        if key in seen or (is_movie and not wants_movies) or (not is_movie and not wants_series):
            continue
        if key[1] == "library" and not prune_untracked:
            # Une analyse Radarr/Sonarr ne connaît pas les médias non suivis :
            # les élaguer ici les ferait disparaître à chaque analyse.
            continue
        for table in (ImportIssue, MediaRequest, MediaWatch, Torrent, MediaFile):
            session.exec(delete(table).where(col(table.media_id) == row.id))
        session.delete(row)
    session.commit()

    medias = list(session.exec(select(Media)).all())
    matched = _reattach_known_torrents(session, settings, medias)
    _recompute_statuses(session, medias)
    session.commit()
    return len(list(session.exec(select(Torrent)).all())), matched


def _reattach_known_torrents(session: Session, settings: Settings, medias: list[Media]) -> int:
    """Rejoue le rattachement avec les torrents déjà connus : les fichiers de
    bibliothèque viennent de changer, donc l'état hardlinké et réparable de
    chaque torrent doit être réévalué. Aucun appel au client torrent."""
    index_by_media_id = _media_index(medias)
    hash_to_index = {
        t.hash.lower(): index_by_media_id[t.media_id]
        for t in session.exec(select(Torrent)).all()
        if t.media_id in index_by_media_id and t.hash
    }
    fetched = torrents_from_cache(session)
    by_media = attach_torrents(settings, _views(session, medias), fetched, hash_to_index)

    session.exec(delete(Torrent))
    session.commit()
    matched = 0
    for media, torrents in zip(medias, by_media, strict=True):
        for torrent in torrents:
            torrent.media_id = row_id(media)
            session.add(torrent)
            matched += 1
    session.commit()
    return matched
