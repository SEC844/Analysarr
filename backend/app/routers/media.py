import json
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session, select

from app.clients.emby import media_server_client
from app.clients.torrent import TorrentAuthError, torrent_client_configured, torrent_client_name
from app.database import get_session
from app.models.media import ImportIssue, Media, MediaFile, MediaType, Torrent
from app.models.settings import Settings
from app.schemas.activity import ActionStepRead
from app.schemas.media import (
    ArrCandidateRead,
    ArrLinkPreview,
    ArrLinkRequest,
    ArrLinkResult,
    ArrQualityProfile,
    CrossSeedSearchResult,
    DeleteExecuteResult,
    DeleteStepResult,
    DeletePreview,
    HardlinkRepairPreview,
    HardlinkRepairResult,
    ImportIssueRead,
    ImportRetryResult,
    MediaDeleteFootprint,
    MediaDeleteSelection,
    MediaDeleteSelectionResult,
    MediaDetail,
    MediaFileRead,
    MediaListItem,
    MediaListResponse,
    MediaRescanResultRead,
    MediaWatchStats,
    TorrentRead,
    TrackerRead,
)
from app.services.arr_instances import instance_names
from app.services.arr_link import ArrLinkError, build_link_preview, link_media
from app.services.cascade_delete import build_delete_preview, execute_delete
from app.services.cross_seed import trigger_cross_seed_search
from app.services.hardlink_repair import build_repair_preview, execute_repair
from app.services.queue_issues import execute_import_retry
from app.services.media_delete import build_delete_footprint, execute_media_delete, reclaimed_bytes
from app.services.media_rescan import rescan_media
from app.services.poster_cache import read_cached_poster, safe_image_type, write_cached_poster
from app.services.scan import launch_scan
from app.services.action_log import MediaRef, record_action
from app.services.notifications import action_notification, channel_targets, notification_language, notify
from app.services.seer import build_requests_read, seer_configured
from app.services.watch_stats import as_utc, build_watch_stats, refresh_media_watch

router = APIRouter()


def _is_cross_seed(torrent: Torrent) -> bool:
    haystacks = (torrent.category, torrent.save_path, torrent.content_path)
    return any(h and "cross-seed" in h.lower() for h in haystacks)


def _to_list_item(media: Media, seer_enabled: bool = False, names: dict[int, str] | None = None) -> MediaListItem:
    return MediaListItem(
        id=media.id,
        media_type=media.media_type.value,
        title=media.title,
        year=media.year,
        statuses=[s for s in media.statuses.split(",") if s],
        reclaimable_bytes=media.reclaimable_bytes,
        total_size=media.total_size,
        has_poster=media.has_poster,
        poster_image_tag=media.poster_image_tag,
        last_scanned_at=media.last_scanned_at,
        has_emby_item=bool(media.emby_item_id),
        date_added=as_utc(media.emby_date_added),
        watch_user_count=media.watch_user_count,
        watch_played_count=media.watch_played_count,
        watch_in_progress_count=media.watch_in_progress_count,
        last_played_at=as_utc(media.last_played_at),
        # Seer désactivé : aucune trace dans l'interface, même d'un scan passé.
        requested_by=media.requested_by if seer_enabled else None,
        arr_instance_name=(names or {}).get(media.arr_instance_id) if media.arr_instance_id is not None else None,
    )


def _matches_watch_filter(media: Media, watch: str) -> bool:
    if watch == "never":
        return media.watch_user_count > 0 and media.watch_played_count == 0 and media.watch_in_progress_count == 0
    if watch == "in_progress":
        return media.watch_in_progress_count > 0
    if watch == "all":
        return media.watch_user_count > 0 and media.watch_played_count == media.watch_user_count
    return True


def _idle_since(media: Media) -> datetime | None:
    """Dernière activité connue : dernière lecture, sinon date d'ajout."""
    return as_utc(media.last_played_at) or as_utc(media.emby_date_added)


@router.get("", response_model=MediaListResponse)
def list_media(
    status: Optional[str] = Query(None, description="doublon | orphelin_qbit | non_hardlink | tracker_unique | sain"),
    media_type: Optional[str] = Query(None, description="movie | series"),
    watch: Optional[str] = Query(None, description="never | in_progress | all"),
    search: Optional[str] = None,
    sort: str = Query("title", description="title | year | size | last_played | cleanup"),
    session: Session = Depends(get_session),
) -> MediaListResponse:
    medias = list(session.exec(select(Media)).all())

    if media_type:
        medias = [m for m in medias if m.media_type.value == media_type]
    if search:
        needle = search.lower()
        medias = [m for m in medias if needle in m.title.lower()]
    if status:
        if status == "sain":
            medias = [m for m in medias if not m.statuses]
        else:
            medias = [m for m in medias if status in m.statuses.split(",")]
    if watch:
        medias = [m for m in medias if _matches_watch_filter(m, watch)]

    now = datetime.now(timezone.utc)
    if sort == "year":
        medias.sort(key=lambda m: m.year or 0, reverse=True)
    elif sort == "size":
        medias.sort(key=lambda m: m.reclaimable_bytes, reverse=True)
    elif sort == "last_played":
        # Plus longtemps sans lecture en premier ; jamais lus d'abord, du plus
        # anciennement ajouté au plus récent.
        medias.sort(key=lambda m: (m.last_played_at is not None, _idle_since(m) or now))
    elif sort == "cleanup":
        # Candidats au nettoyage : poids × jours sans activité — un gros
        # fichier jamais regardé depuis un an passe devant un petit fichier vu
        # le mois dernier.
        def cleanup_score(m: Media) -> float:
            since = _idle_since(m)
            idle_days = max((now - since).days, 1) if since else 1
            return (m.total_size or 0) * idle_days

        medias.sort(key=cleanup_score, reverse=True)
    else:
        medias.sort(key=lambda m: m.title.lower())

    seer_enabled = seer_configured(session.get(Settings, 1))
    names = instance_names(session)
    return MediaListResponse(items=[_to_list_item(m, seer_enabled, names) for m in medias], total=len(medias))


def _media_and_settings(media_id: int, session: Session) -> tuple[Media, Settings]:
    """Média et configuration, ou l'erreur HTTP correspondante : préambule
    commun à toutes les routes qui agissent sur un média."""
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    settings = session.get(Settings, 1)
    if settings is None:
        raise HTTPException(400, "Configuration manquante.")
    return media, settings


@router.get("/{media_id}", response_model=MediaDetail)
def get_media(media_id: int, session: Session = Depends(get_session)) -> MediaDetail:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")

    files = session.exec(select(MediaFile).where(MediaFile.media_id == media_id)).all()
    torrents = session.exec(select(Torrent).where(Torrent.media_id == media_id)).all()
    seer_enabled = seer_configured(session.get(Settings, 1))

    issues = session.exec(select(ImportIssue).where(ImportIssue.media_id == media_id)).all()

    return MediaDetail(
        **_to_list_item(media, seer_enabled, instance_names(session)).model_dump(),
        requests=build_requests_read(session, media) if seer_enabled else [],
        import_issues=[
            ImportIssueRead(
                id=i.id,
                kind=i.kind,
                title=i.title,
                state=i.state,
                reason=i.reason,
                size=i.size,
                episode_label=i.episode_label,
                # Relançable dès que Sonarr/Radarr peut retrouver le
                # téléchargement, par son identifiant ou par son dossier.
                can_retry=i.kind == "import" and bool(i.download_id or i.output_path),
            )
            for i in issues
        ],
        radarr_id=media.radarr_id,
        sonarr_id=media.sonarr_id,
        emby_item_id=media.emby_item_id,
        tmdb_id=media.tmdb_id,
        tvdb_id=media.tvdb_id,
        imdb_id=media.imdb_id,
        files=[
            MediaFileRead(id=f.id, path=f.path, size=f.size, episode_label=f.episode_label, is_current=f.is_current)
            for f in files
        ],
        torrents=[
            TorrentRead(
                id=t.id,
                hash=t.hash,
                name=t.name,
                save_path=t.save_path,
                content_path=t.content_path,
                size=t.size,
                is_cross_seed=_is_cross_seed(t),
                is_hardlinked=t.is_hardlinked,
                matched_by_name=t.matched_by_name,
                repairable=t.repairable,
                ratio=t.ratio,
                seeders=t.seeders,
                leechers=t.leechers,
                added_on=t.added_on,
                completed_on=t.completed_on,
                trackers=[TrackerRead(**d) for d in json.loads(t.trackers_json)],
            )
            for t in torrents
        ],
        missing_emby_episodes=[e for e in media.missing_emby_episodes.split(",") if e],
    )


@router.get("/{media_id}/poster")
async def get_poster(media_id: int, session: Session = Depends(get_session)) -> Response:
    media = session.get(Media, media_id)
    if media is None or not media.emby_item_id:
        raise HTTPException(404, "Pas de jaquette disponible.")

    cached = read_cached_poster(media.emby_item_id, media.poster_image_tag)
    if cached is not None:
        content, content_type = cached
    else:
        emby = media_server_client(session.get(Settings, 1))
        if emby is None:
            raise HTTPException(404, "Serveur multimédia non configuré.")
        result = await emby.fetch_poster(media.emby_item_id)
        if result is None:
            raise HTTPException(404, "Jaquette introuvable.")
        content = result[0]
        content_type = safe_image_type(result[1])
        if content_type is None:
            raise HTTPException(404, "Jaquette introuvable.")
        write_cached_poster(media.emby_item_id, media.poster_image_tag, content, content_type)

    # L'URL est déjà propre à cette version précise de la jaquette (voir
    # `?v=` côté frontend, lib/api.ts::posterUrl) : le contenu d'UNE URL
    # donnée ne change jamais, le navigateur peut donc la garder en cache
    # indéfiniment sans risque de servir une jaquette périmée.
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{media_id}/watch", response_model=MediaWatchStats)
async def get_media_watch(media_id: int, session: Session = Depends(get_session)) -> MediaWatchStats:
    """Visionnage par utilisateur Emby, rafraîchi en direct à l'ouverture de
    la fiche (repli sur le dernier scan si Emby est injoignable)."""
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    settings = session.get(Settings, 1)
    live = await refresh_media_watch(session, media, settings)
    return build_watch_stats(session, media, settings, live)


@router.post("/{media_id}/delete/preview", response_model=DeletePreview)
def delete_preview(media_id: int, session: Session = Depends(get_session)) -> DeletePreview:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    return build_delete_preview(session, media)


@router.post("/{media_id}/delete/execute", response_model=DeleteExecuteResult)
async def delete_execute(media_id: int, session: Session = Depends(get_session)) -> DeleteExecuteResult:
    media, settings = _media_and_settings(media_id, session)
    ref = MediaRef.of(media)
    freed = build_delete_preview(session, media).total_reclaimable_bytes
    result = await execute_delete(session, media, settings)
    _log_and_notify(session, settings, "cascade_delete", ref, result.steps, freed)
    return result


@router.get("/{media_id}/delete-selection/footprint", response_model=MediaDeleteFootprint)
async def delete_selection_footprint(media_id: int, session: Session = Depends(get_session)) -> MediaDeleteFootprint:
    media, settings = _media_and_settings(media_id, session)
    return await build_delete_footprint(session, media, settings)


@router.post("/{media_id}/delete-selection", response_model=MediaDeleteSelectionResult)
async def delete_selection(
    media_id: int, payload: MediaDeleteSelection, session: Session = Depends(get_session)
) -> MediaDeleteSelectionResult:
    # Un média sans fichier ni torrent n'a rien à cocher : seuls son suivi
    # Sonarr/Radarr et sa demande Seer peuvent encore être retirés.
    if not payload.torrent_ids and not payload.media_file_ids and not payload.remove_from_arr:
        raise HTTPException(400, "Aucun élément sélectionné.")
    media, settings = _media_and_settings(media_id, session)
    ref = MediaRef.of(media)
    # Empreinte calculée AVANT la suppression : les inodes ne sont plus
    # lisibles ensuite.
    footprint = await build_delete_footprint(session, media, settings)
    freed = reclaimed_bytes(footprint, payload.torrent_ids, payload.media_file_ids)
    result = await execute_media_delete(session, media, settings, payload)
    _log_and_notify(session, settings, "delete_selection", ref, result.steps, freed)
    return result


@router.get("/{media_id}/arr-link", response_model=ArrLinkPreview)
async def arr_link_preview(media_id: int, session: Session = Depends(get_session)) -> ArrLinkPreview:
    """Ce qu'Analysarr propose pour rattacher ce média à Sonarr/Radarr."""
    media, settings = _media_and_settings(media_id, session)
    try:
        preview = await build_link_preview(session, settings, media)
    except ArrLinkError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ArrLinkPreview(
        service=preview.service,
        instance_name=preview.instance_name,
        candidates=[
            ArrCandidateRead(
                key=candidate.key,
                title=candidate.title,
                year=candidate.year,
                tmdb_id=candidate.tmdb_id,
                tvdb_id=candidate.tvdb_id,
                imdb_id=candidate.imdb_id,
                confidence=candidate.confidence,
            )
            for candidate in preview.candidates
        ],
        folder=preview.folder,
        root_folder=preview.root_folder,
        folder_unmapped=preview.folder_unmapped,
        quality_profiles=[ArrQualityProfile(id=profile_id, name=name) for profile_id, name in preview.quality_profiles],
        suggested_profile=preview.suggested_profile,
    )


@router.post("/{media_id}/arr-link", response_model=ArrLinkResult)
async def arr_link(media_id: int, payload: ArrLinkRequest, session: Session = Depends(get_session)) -> ArrLinkResult:
    """Ajoute le média dans Sonarr/Radarr avec le dossier qui contient déjà ses
    fichiers, puis relit ce média pour qu'il apparaisse comme suivi."""
    media, settings = _media_and_settings(media_id, session)
    ref = MediaRef.of(media)
    try:
        title = await link_media(
            session,
            settings,
            media,
            candidate_key=payload.candidate_key,
            quality_profile_id=payload.quality_profile_id,
            monitor=payload.monitor,
        )
    except ArrLinkError as exc:
        raise HTTPException(400, str(exc)) from exc

    service = "radarr" if media.media_type == MediaType.movie else "sonarr"
    _log_and_notify(
        session, settings, "arr_link", ref, [DeleteStepResult(kind="arr_media", label=title, success=True)]
    )
    # Le média doit réapparaître suivi sans scan manuel : seule une analyse du
    # service concerné peut lui donner son identité Sonarr/Radarr.
    launch_scan(scope=service)
    return ArrLinkResult(title=title, service=service)


@router.post("/{media_id}/cross-seed-search", response_model=CrossSeedSearchResult)
async def cross_seed_search(
    media_id: int,
    scope: str = Query("episode", description="episode | season | series (season/series : séries uniquement)"),
    session: Session = Depends(get_session),
) -> CrossSeedSearchResult:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    if scope not in ("episode", "season", "series"):
        raise HTTPException(400, "scope invalide : episode | season | series.")
    settings = session.get(Settings, 1)
    if settings is None:
        raise HTTPException(400, "Configuration manquante.")
    torrents = session.exec(select(Torrent).where(Torrent.media_id == media_id)).all()
    files = session.exec(select(MediaFile).where(MediaFile.media_id == media_id)).all()
    result = await trigger_cross_seed_search(settings, [t.hash for t in torrents], list(files), scope=scope)
    steps = [ActionStepRead(label=f"cross-seed ({scope})", success=True)] * result.triggered + [
        ActionStepRead(label=f"cross-seed ({scope})", success=False, error=error) for error in result.errors
    ]
    _log_and_notify(session, settings, "cross_seed_search", MediaRef.of(media), steps)
    return result


@router.post("/{media_id}/rescan", response_model=MediaRescanResultRead)
async def rescan_one_media(media_id: int, session: Session = Depends(get_session)) -> MediaRescanResultRead:
    """Analyse ciblée d'un seul média : Sonarr/Radarr, serveur multimédia,
    file d'attente et torrents de CE média uniquement. Ne touche à aucun autre
    média et conserve l'identifiant de la fiche."""
    from app.services.scan import is_scan_running  # import différé : évite un cycle

    media, settings = _media_and_settings(media_id, session)
    if is_scan_running():
        raise HTTPException(409, "Une analyse est déjà en cours.")

    try:
        result = await rescan_media(session, settings, media)
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(502, f"{type(exc).__name__} : {exc}") from exc
    return MediaRescanResultRead(
        media_deleted=result.media_deleted,
        files=result.files,
        torrents=result.torrents,
        import_issues=result.import_issues,
        statuses=result.statuses,
    )


@router.post("/{media_id}/retry-import", response_model=ImportRetryResult)
async def retry_import_route(media_id: int, session: Session = Depends(get_session)) -> ImportRetryResult:
    """Relance l'import des téléchargements que Sonarr/Radarr n'a pas réussi à
    ranger. Seul l'identifiant du média vient de l'utilisateur : les
    téléchargements ciblés sont ceux que le dernier scan a relevés dans la file
    d'attente de Sonarr/Radarr."""
    media, settings = _media_and_settings(media_id, session)

    steps, imported = await execute_import_retry(session, media, settings)
    if not steps:
        raise HTTPException(400, "Aucun import bloqué pour ce média.")

    read_steps = [ActionStepRead(label=s.label, success=s.success, error=s.error) for s in steps]
    _log_and_notify(session, settings, "import_retry", MediaRef.of(media), read_steps)
    return ImportRetryResult(steps=steps, imported_files=imported)


@router.post("/{media_id}/hardlink-repair/preview", response_model=HardlinkRepairPreview)
async def hardlink_repair_preview(media_id: int, session: Session = Depends(get_session)) -> HardlinkRepairPreview:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    settings = session.get(Settings, 1)
    if not torrent_client_configured(settings):
        raise HTTPException(400, f"{torrent_client_name(settings)} non configuré.")
    try:
        return await build_repair_preview(session, media, settings)
    except TorrentAuthError as exc:
        raise HTTPException(502, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"qBittorrent injoignable : {exc}") from exc


@router.post("/{media_id}/hardlink-repair/execute", response_model=HardlinkRepairResult)
async def hardlink_repair_execute(media_id: int, session: Session = Depends(get_session)) -> HardlinkRepairResult:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(404, "Média introuvable.")
    settings = session.get(Settings, 1)
    if not torrent_client_configured(settings):
        raise HTTPException(400, f"{torrent_client_name(settings)} non configuré.")
    ref = MediaRef.of(media)
    try:
        result = await execute_repair(session, media, settings)
    except TorrentAuthError as exc:
        raise HTTPException(502, str(exc)) from exc
    _log_and_notify(session, settings, "hardlink_repair", ref, result.steps, result.freed_bytes)
    return result


def _log_and_notify(session: Session, settings: Settings, action: str, ref: MediaRef, steps: list, freed: int | None = None) -> None:
    """Toute action effectuée est tracée dans l'historique et, si activé,
    notifiée (Discord/ntfy/Gotify) avec la jaquette du média. L'espace libéré
    n'est retenu que si aucune étape n'a échoué : il serait sinon surestimé."""
    if any(not s.success for s in steps):
        freed = None
    entry = record_action(session, action, ref, steps, freed_bytes=freed or None)
    notification = action_notification(
        notification_language(settings),
        action,
        ref,
        success=entry.success_count,
        failures=entry.failure_count,
        freed_bytes=entry.freed_bytes,
        steps=steps,
    )
    notify(channel_targets(session), action, notification, poster=ref, settings=settings)
