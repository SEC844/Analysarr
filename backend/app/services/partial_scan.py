"""Analyses partielles : rafraîchir UNE source de données sans reconstruire
toute la bibliothèque.

Principe non négociable : chaque périmètre laisse la base dans un état
cohérent. Une analyse partielle ne met à jour que ce qu'elle a réellement
relu, puis recalcule les statuts des médias concernés à partir du cache pour
le reste. Aucun statut ne peut donc rester calculé sur des données à moitié
rafraîchies.

Périmètres étroits gérés ici (le média n'est jamais ajouté ni supprimé, seules
ses données changent, et les identifiants de fiche restent valides) :

- `torrents` : relit le client torrent, rattache et recalcule les statuts ;
- `queue`    : relit la file d'attente Sonarr/Radarr (imports bloqués) ;
- `watch`    : relit les statistiques de visionnage ;
- `seer`     : relit les demandes Seer.

Les périmètres `full` et `library` passent par le scan complet
(`services/scan.py`) : eux seuls peuvent ajouter ou retirer des médias, parce
que la liste des médias vient de Sonarr/Radarr.
"""

from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, delete, select

from app.clients.emby import EmbyClient, media_server_name
from app.clients.torrent import torrent_client_configured, torrent_client_name
from app.database import engine
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
from app.services.queue_issues import index_queue_issues, issue_rows_for
from app.services.scan_scopes import NARROW_SCOPES
from app.services.seer import build_request_rows, index_requests, seer_configured
from app.services.torrent_match import MediaView, attach_torrents, fetch_torrents
from app.services.watch_stats import (
    apply_aggregates,
    build_watch_rows,
    collect_watch_data,
    excluded_user_ids,
    users_from_api,
)

__all__ = ["run_narrow_scan"]


def _media_index(medias: list[Media]) -> dict[int, int]:
    return {media.id: i for i, media in enumerate(medias) if media.id is not None}


def _recompute_statuses(session: Session, medias: list[Media]) -> None:
    """Recalcule statuts, espace récupérable et poids de chaque média à partir
    de ce que la base contient MAINTENANT. Appelé à la fin de chaque analyse
    partielle : les statuts croisent plusieurs sources, ils ne peuvent pas
    rester figés parce qu'une seule a été relue."""
    from app.services.scan import compute_statuses, current_files_size  # import différé : évite un cycle

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
    for media, torrents in zip(medias, by_media):
        for torrent in torrents:
            torrent.media_id = media.id
            session.add(torrent)
            matched += 1
    session.commit()
    return len(fetched), matched


async def _refresh_queue(session: Session, settings: Settings, medias: list[Media]) -> None:
    """Relit la file d'attente de chaque instance Sonarr/Radarr. Échec
    silencieux par instance, comme pendant un scan complet."""
    issues_by_key: dict[tuple[str, int | None, int], list[dict[str, Any]]] = {}
    for kind in ("radarr", "sonarr"):
        for target in arr_targets(session, settings, kind):
            client = target.radarr() if kind == "radarr" else target.sonarr()
            try:
                records = await client.get_queue()
            except Exception:  # noqa: BLE001 - informatif, ne doit jamais faire échouer l'analyse
                continue
            key = "movieId" if kind == "radarr" else "seriesId"
            for arr_id, records_for_id in index_queue_issues(records, key).items():
                issues_by_key.setdefault((kind, target.instance_id, arr_id), []).extend(records_for_id)

    session.exec(delete(ImportIssue))
    session.commit()
    for media in medias:
        kind = "radarr" if media.media_type == MediaType.movie else "sonarr"
        arr_id = media.radarr_id if kind == "radarr" else media.sonarr_id
        if arr_id is None:
            continue
        records = issues_by_key.get((kind, media.arr_instance_id, arr_id), [])
        for row in issue_rows_for(records):
            row.media_id = media.id
            session.add(row)
    session.commit()


async def _refresh_watch(session: Session, settings: Settings, medias: list[Media]) -> None:
    """Relit les statistiques de visionnage. Échec silencieux : des stats
    vides valent mieux qu'une analyse en erreur."""
    emby = EmbyClient(settings.emby_url, settings.emby_api_key, settings.media_server)
    try:
        users = users_from_api(await emby.get_users())
        data = await collect_watch_data(emby, users)
    except Exception:  # noqa: BLE001 - informatif
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
            row.media_id = media.id
            session.add(row)
        apply_aggregates(media, rows, excluded)
        session.add(media)
    session.commit()


async def _refresh_seer(session: Session, settings: Settings, medias: list[Media]) -> None:
    """Relit les demandes Seer. Échec silencieux, comme pendant un scan."""
    from app.clients.seer import SeerClient

    try:
        index = index_requests(await SeerClient(settings.seer_url, settings.seer_api_key).get_requests())
    except Exception:  # noqa: BLE001 - informatif
        return

    session.exec(delete(MediaRequest))
    session.commit()
    for media in medias:
        rows = build_request_rows(media, index)
        for row in rows:
            row.media_id = media.id
            session.add(row)
        session.add(media)
    session.commit()


def _missing_service(scope: str, settings: Settings) -> str | None:
    """Service indispensable au périmètre demandé, s'il n'est pas configuré."""
    if scope == "torrents" and not torrent_client_configured(settings):
        return torrent_client_name(settings)
    if scope == "watch" and not (settings.emby_url and settings.emby_api_key):
        return media_server_name(settings)
    if scope == "seer" and not seer_configured(settings):
        return "Seer"
    return None


async def run_narrow_scan(scope: str, trigger: str = "manual") -> None:
    """Analyse partielle d'une seule source. L'appelant détient déjà le verrou
    de scan (voir `services/scan.run_scan`) : une analyse partielle et un scan
    complet ne tournent jamais en même temps."""
    assert scope in NARROW_SCOPES

    with Session(engine) as session:
        settings = session.get(Settings, 1)
        if settings is not None:
            session.expunge(settings)
        channels: list[ChannelTarget] = channel_targets(session)
        run = ScanRun(status=ScanStatus.running, trigger=trigger, scope=scope)
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    await scan_events.publish({"type": "started", "run_id": run_id, "scope": scope})

    from app.services.scan import _fail_scan  # import différé : évite un cycle

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

            run = session.get(ScanRun, run_id)
            assert run is not None
            run.status = ScanStatus.completed
            run.finished_at = datetime.now(timezone.utc)
            run.media_count = len(medias)
            run.duplicate_count = sum(1 for m in medias if "doublon" in m.statuses.split(","))
            run.orphan_count = sum(1 for m in medias if "orphelin_qbit" in m.statuses.split(","))
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
