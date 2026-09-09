"""Suppression manuelle et sélective d'un média : contrairement à
cascade_delete.py (qui n'agit que sur les doublons/orphelins détectés
automatiquement), c'est ici l'utilisateur qui choisit lui-même quels
torrents et/ou quels fichiers de bibliothèque supprimer — un épisode, une
saison entière, toute la série, ou le film — avec la possibilité d'arrêter
aussi le suivi Sonarr/Radarr pour éviter un retéléchargement automatique."""

import os

import httpx
from sqlmodel import Session, select

from app.clients.arr import RadarrClient, SonarrClient
from app.clients.qbittorrent import QbittorrentAuthError, QbittorrentClient
from app.models.media import Media, MediaFile, MediaType, Torrent
from app.models.settings import Settings
from app.schemas.media import DeleteStepResult, MediaDeleteSelection, MediaDeleteSelectionResult
from app.services.scan import compute_statuses


async def _delete_torrents(
    torrents: list[Torrent], settings: Settings, steps: list[DeleteStepResult], session: Session
) -> None:
    if not torrents:
        return
    try:
        async with QbittorrentClient(
            settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password
        ) as qbit:
            await qbit.delete_torrents([t.hash for t in torrents], delete_files=True)
        for t in torrents:
            session.delete(t)
            steps.append(DeleteStepResult(kind="torrent", label=t.name, success=True))
    except (QbittorrentAuthError, httpx.HTTPError) as exc:
        for t in torrents:
            steps.append(DeleteStepResult(kind="torrent", label=t.name, success=False, error=str(exc)))


async def _delete_movie_files(
    files: list[MediaFile],
    media: Media,
    settings: Settings,
    remove_from_arr: bool,
    steps: list[DeleteStepResult],
    session: Session,
) -> None:
    radarr = (
        RadarrClient(settings.radarr_url, settings.radarr_api_key)
        if settings.radarr_url and settings.radarr_api_key
        else None
    )
    for f in files:
        try:
            if f.arr_file_id and radarr is not None and media.radarr_id and remove_from_arr:
                await radarr.delete_movie(media.radarr_id)  # supprime aussi le fichier
            elif f.arr_file_id and radarr is not None:
                await radarr.delete_movie_file(f.arr_file_id)
            else:
                # Fichier en trop jamais suivi par Radarr (doublon) : rien à
                # supprimer côté Radarr, juste le fichier lui-même.
                os.remove(f.path)
            session.delete(f)
            steps.append(DeleteStepResult(kind="library_file", label=f.path, success=True))
        except (httpx.HTTPError, OSError) as exc:
            steps.append(DeleteStepResult(kind="library_file", label=f.path, success=False, error=str(exc)))


async def _delete_episode_files(
    files: list[MediaFile], settings: Settings, remove_from_arr: bool, steps: list[DeleteStepResult], session: Session
) -> None:
    sonarr = (
        SonarrClient(settings.sonarr_url, settings.sonarr_api_key)
        if settings.sonarr_url and settings.sonarr_api_key
        else None
    )
    episodes_to_unmonitor: list[int] = []
    for f in files:
        try:
            if f.arr_file_id and sonarr is not None:
                await sonarr.delete_episode_file(f.arr_file_id)
                if remove_from_arr and f.sonarr_episode_id:
                    episodes_to_unmonitor.append(f.sonarr_episode_id)
            else:
                os.remove(f.path)
            session.delete(f)
            steps.append(DeleteStepResult(kind="library_file", label=f.episode_label or f.path, success=True))
        except (httpx.HTTPError, OSError) as exc:
            steps.append(
                DeleteStepResult(kind="library_file", label=f.episode_label or f.path, success=False, error=str(exc))
            )

    if episodes_to_unmonitor and sonarr is not None:
        try:
            await sonarr.set_episodes_monitored(episodes_to_unmonitor, monitored=False)
        except httpx.HTTPError as exc:
            steps.append(
                DeleteStepResult(
                    kind="sonarr_monitor",
                    label=f"{len(episodes_to_unmonitor)} épisode(s) à démonitorer",
                    success=False,
                    error=str(exc),
                )
            )


async def execute_media_delete(
    session: Session, media: Media, settings: Settings, selection: MediaDeleteSelection
) -> MediaDeleteSelectionResult:
    torrents = (
        session.exec(
            select(Torrent).where(Torrent.media_id == media.id, Torrent.id.in_(selection.torrent_ids))
        ).all()
        if selection.torrent_ids
        else []
    )
    files = (
        session.exec(
            select(MediaFile).where(MediaFile.media_id == media.id, MediaFile.id.in_(selection.media_file_ids))
        ).all()
        if selection.media_file_ids
        else []
    )

    steps: list[DeleteStepResult] = []
    await _delete_torrents(torrents, settings, steps, session)
    if files:
        if media.media_type == MediaType.movie:
            await _delete_movie_files(files, media, settings, selection.remove_from_arr, steps, session)
        else:
            await _delete_episode_files(files, settings, selection.remove_from_arr, steps, session)

    session.commit()

    # Recalcul immédiat : sans ça, la fiche resterait fausse (statuts et
    # espace récupérable) jusqu'au prochain scan complet.
    remaining_files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    remaining_torrents = session.exec(select(Torrent).where(Torrent.media_id == media.id)).all()
    statuses, reclaimable = compute_statuses(list(remaining_files), list(remaining_torrents), bool(media.emby_item_id))
    media.statuses = ",".join(sorted(statuses))
    media.reclaimable_bytes = reclaimable
    session.add(media)
    session.commit()

    return MediaDeleteSelectionResult(steps=steps)
