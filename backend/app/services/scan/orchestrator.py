"""Déroulé d'un scan complet : enregistrement du résultat, notifications,
automatisations. Le verrou unique et le lancement vivent dans le package
(`app.services.scan`)."""

import logging
from datetime import UTC, datetime

from sqlmodel import Session, delete, select

from app.clients.emby import media_server_name
from app.clients.torrent import torrent_client_configured, torrent_client_name
from app.database import engine
from app.models.ids import row_id
from app.models.media import (
    EmbyUser,
    ImportIssue,
    Media,
    MediaFile,
    MediaRequest,
    MediaWatch,
    ScanRun,
    ScanStatus,
    Torrent,
)
from app.models.settings import Settings
from app.services.arr_instances import ArrTarget, arr_targets
from app.services.events import scan_events
from app.services.notifications import (
    ChannelTarget,
    Notification,
    automations_paused_notification,
    channel_targets,
    detection_notification,
    notification_language,
    notify,
    scan_completed_notification,
    scan_failed_notification,
)
from app.services.scan.collect import _collect
from app.services.scan.results import MediaBuildResult
from app.services.scan.statuses import DETECTION_EVENTS
from app.services.torrent_match import FetchedTorrents, persist_files

logger = logging.getLogger("analysarr.scan")



async def _run_scan_impl(trigger: str = "manual", scope: str = "full") -> None:
    settings, radarr_targets, sonarr_targets, channels, run_id = _start_run(trigger, scope)
    await scan_events.publish({"type": "started", "run_id": run_id})

    if settings is None:
        await _fail_scan(run_id, "Aucune configuration enregistrée.", channels, settings)
        return
    missing = _missing_services(settings)
    if missing:
        await _fail_scan(run_id, f"Services non configurés : {', '.join(missing)}.", channels, settings)
        return

    try:
        results, fetched_torrents, emby_users = await _collect(settings, run_id, radarr_targets, sonarr_targets)
    except Exception as exc:  # noqa: BLE001 - toute erreur externe doit être reportée proprement, pas planter le process
        await _fail_scan(run_id, f"{type(exc).__name__} : {exc}", channels, settings)
        return

    await scan_events.publish({"type": "progress", "run_id": run_id, "stage": "enregistrement"})
    previously_flagged, counts, summary = _store_results(run_id, settings, results, fetched_torrents, emby_users)

    await scan_events.publish({"type": "completed", "run_id": run_id, **counts})
    notify(channels, "scan_completed", summary)
    _notify_new_detections(channels, settings, results, previously_flagged)
    await _run_automations(channels, run_id)


def _start_run(
    trigger: str, scope: str
) -> tuple[Settings | None, list[ArrTarget], list[ArrTarget], list[ChannelTarget], int]:
    """Réglages, instances et canaux figés pour toute l'analyse, et ligne
    d'historique de l'analyse (en cours)."""
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
        run = ScanRun(status=ScanStatus.running, trigger=trigger, scope=scope)
        session.add(run)
        session.commit()
        session.refresh(run)
        return settings, radarr_targets, sonarr_targets, channels, row_id(run)


def _missing_services(settings: Settings) -> list[str]:
    """Services requis mais non configurés : le scan complet a besoin de tous."""
    required = [
        (media_server_name(settings), bool(settings.emby_url and settings.emby_api_key)),
        ("Sonarr", bool(settings.sonarr_url and settings.sonarr_api_key)),
        ("Radarr", bool(settings.radarr_url and settings.radarr_api_key)),
        (torrent_client_name(settings), torrent_client_configured(settings)),
    ]
    return [name for name, ok in required if not ok]


def _store_results(
    run_id: int,
    settings: Settings,
    results: list[MediaBuildResult],
    fetched_torrents: FetchedTorrents,
    emby_users: list[EmbyUser],
) -> tuple[dict[str, set[tuple[str, str, int | None]]], dict[str, int], Notification]:
    """Remplace le cache par le résultat du scan et clôt l'analyse. Une seule
    session : les médias restent lisibles pour les notifications qui suivent."""
    with Session(engine) as session:
        previously_flagged = _flagged_before(session)
        _replace_cache(session, results, emby_users)
        # Fichiers des torrents : mémorisés pour que les analyses par service
        # recalculent les hardlinks sans rappeler le client torrent.
        persist_files(session, fetched_torrents)
        run = _complete_run(session, run_id, results, fetched_torrents)
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
    return previously_flagged, counts, summary


def _flagged_before(session: Session) -> dict[str, set[tuple[str, str, int | None]]]:
    """Avant de remplacer le cache : quels médias portaient DÉJÀ chaque
    statut ? Seule une NOUVELLE apparition est notifiée. Clé stable d'un scan
    à l'autre : les ids sont régénérés."""
    previous_medias = list(session.exec(select(Media)).all())
    return {
        event: {_media_key(media) for media in previous_medias if status in media.statuses.split(",")}
        for event, status in DETECTION_EVENTS.items()
    }


def _replace_cache(session: Session, results: list[MediaBuildResult], emby_users: list[EmbyUser]) -> None:
    # Tables filles d'abord : les clés étrangères pointent vers `media`.
    for table in (ImportIssue, MediaRequest, MediaWatch, EmbyUser, Torrent, MediaFile, Media):
        session.exec(delete(table))
    session.commit()

    session.add_all(emby_users)
    session.add_all([result.media for result in results])
    session.commit()
    for result in results:
        session.refresh(result.media)
        media_id = row_id(result.media)
        for row in (*result.files, *result.torrents, *result.watches, *result.requests, *result.import_issues):
            row.media_id = media_id
            session.add(row)
    session.commit()


def _complete_run(
    session: Session, run_id: int, results: list[MediaBuildResult], fetched_torrents: FetchedTorrents
) -> ScanRun:
    run = session.get(ScanRun, run_id)
    if run is None:
        raise RuntimeError(f"Analyse {run_id} introuvable en base")

    def count(status: str) -> int:
        return sum(1 for r in results if status in r.media.statuses.split(","))

    run.status = ScanStatus.completed
    run.finished_at = datetime.now(UTC)
    run.media_count = len(results)
    run.duplicate_count = count("doublon")
    run.orphan_count = count("orphelin_qbit")
    run.non_hardlink_count = count("non_hardlink")
    run.tracker_unique_count = count("tracker_unique")
    run.qbittorrent_torrent_count = len(fetched_torrents)
    run.qbittorrent_matched_count = sum(len(r.torrents) for r in results)
    session.add(run)
    session.commit()
    return run


def _notify_new_detections(
    channels: list[ChannelTarget],
    settings: Settings,
    results: list[MediaBuildResult],
    previously_flagged: dict[str, set[tuple[str, str, int | None]]],
) -> None:
    for event, status in DETECTION_EVENTS.items():
        newly_flagged = [
            (result.media.title, result.media.reclaimable_bytes)
            for result in results
            if status in result.media.statuses.split(",") and _media_key(result.media) not in previously_flagged[event]
        ]
        if newly_flagged:
            notify(channels, event, detection_notification(notification_language(settings), event, newly_flagged))


def _duration_seconds(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    # Relus depuis SQLite : naïfs (UTC), comparés sans fuseau.
    return max(0, int((finished_at.replace(tzinfo=None) - started_at.replace(tzinfo=None)).total_seconds()))


async def _run_automations(channels: list[ChannelTarget], run_id: int) -> None:
    """Règles d'automatisation activées, sur les statuts que ce scan vient de
    calculer — sauf si ce scan a fait basculer une part anormale de la
    bibliothèque (voir services/automation_guard.py)."""
    # import local : évite le cycle scan ↔ automations (les automatisations
    # passent par les modules d'action, qui importent les statuts du scan)
    from app.services import automations
    from app.services.automation_guard import detect_mass_change, is_paused, pause_automations


    with Session(engine) as session:
        settings = session.get(Settings, 1)
        run = session.get(ScanRun, run_id)
        if settings is not None and run is not None and not is_paused(settings):
            change = detect_mass_change(session, run, settings)
            if change is not None:
                pause_automations(session, settings, change)
                notify(
                    channels,
                    "automations_paused",
                    automations_paused_notification(
                        notification_language(settings),
                        status=change.status,
                        previous=change.previous,
                        current=change.current,
                        percent=change.percent,
                    ),
                )
        if is_paused(settings):
            return  # reprise manuelle attendue : aucune règle ne s'exécute
        try:
            await automations.run_automations(session, settings, channels)
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
            run.finished_at = datetime.now(UTC)
            session.add(run)
            session.commit()
    await scan_events.publish({"type": "failed", "run_id": run_id, "message": message})
