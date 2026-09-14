"""Suppression manuelle et sélective d'un média : contrairement à
cascade_delete.py (qui n'agit que sur les doublons/orphelins détectés
automatiquement), c'est ici l'utilisateur qui choisit lui-même quels
torrents et/ou quels fichiers de bibliothèque supprimer — un épisode, une
saison entière, toute la série, ou le film — avec la possibilité d'arrêter
aussi le suivi Sonarr/Radarr pour éviter un retéléchargement automatique."""

import os
import stat as stat_module

import httpx
from sqlmodel import Session, select

from app.clients.arr import RadarrClient, SonarrClient
from app.clients.qbittorrent import QbittorrentAuthError, QbittorrentClient
from app.models.media import Media, MediaFile, MediaType, Torrent
from app.models.settings import Settings
from app.schemas.media import (
    DeleteFootprintItem,
    DeleteStepResult,
    DiskUnit,
    MediaDeleteFootprint,
    MediaDeleteSelection,
    MediaDeleteSelectionResult,
)
from app.services.hardlink import resolve_torrent_files
from app.services.scan import compute_statuses


async def build_delete_footprint(session: Session, media: Media, settings: Settings) -> MediaDeleteFootprint:
    """Résout en direct l'inode de chaque fichier physique de chaque élément
    supprimable (fichiers de bibliothèque, CHAQUE fichier de chaque torrent
    — pas seulement l'inode représentatif stocké au scan, insuffisant pour
    un pack saison, voir build_repair_preview) et les regroupe par inode.

    Un chemin introuvable ou non résolu (dossier, qBittorrent injoignable)
    devient une unité à part, de la taille connue en base : estimation
    prudente plutôt que de faire disparaître l'élément du calcul."""
    files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    torrents = session.exec(select(Torrent).where(Torrent.media_id == media.id)).all()

    units: list[DiskUnit] = []
    unit_by_inode: dict[tuple[int, int], int] = {}

    def unit_for(path: str | None, fallback_size: int | None) -> list[int]:
        if path and os.path.islink(path):
            return []  # supprimer un lien symbolique ne libère aucun espace
        try:
            st = os.stat(path) if path else None
        except OSError:
            st = None
        if st is None or not stat_module.S_ISREG(st.st_mode):
            units.append(DiskUnit(size=fallback_size or 0, links=1))
            return [len(units) - 1]
        key = (st.st_ino, st.st_dev)
        if key not in unit_by_inode:
            unit_by_inode[key] = len(units)
            units.append(DiskUnit(size=st.st_size, links=st.st_nlink))
        return [unit_by_inode[key]]

    file_items = [DeleteFootprintItem(id=f.id, units=unit_for(f.path, f.size)) for f in files]

    torrent_files: dict[int, list[tuple[str, int | None]]] = {}
    qbit_configured = settings.qbittorrent_url and settings.qbittorrent_username and settings.qbittorrent_password
    if torrents and qbit_configured:
        try:
            async with QbittorrentClient(
                settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password
            ) as qbit:
                for t in torrents:
                    torrent_files[t.id] = await resolve_torrent_files(qbit, t)
        except (QbittorrentAuthError, httpx.HTTPError):
            torrent_files.clear()  # repli ci-dessous sur content_path

    torrent_items: list[DeleteFootprintItem] = []
    for t in torrents:
        paths = torrent_files.get(t.id) or await resolve_torrent_files(None, t)
        unit_ids = [i for path, size in paths for i in unit_for(path, size)] if paths else unit_for(None, t.size)
        torrent_items.append(DeleteFootprintItem(id=t.id, units=unit_ids))

    return MediaDeleteFootprint(units=units, torrents=torrent_items, files=file_items)


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


async def _delete_whole_series(
    files: list[MediaFile], media: Media, sonarr: SonarrClient, steps: list[DeleteStepResult], session: Session
) -> None:
    """Toute la série sélectionnée avec retrait de Sonarr : un seul appel
    Sonarr (série + dossier), puis suppression directe des fichiers qui
    subsisteraient hors de son dossier (doublons jamais suivis par Sonarr)."""
    try:
        await sonarr.delete_series(media.sonarr_id)
    except httpx.HTTPError as exc:
        steps.append(DeleteStepResult(kind="sonarr_series", label=media.title, success=False, error=str(exc)))
        return
    steps.append(DeleteStepResult(kind="sonarr_series", label=f"{media.title} retirée de Sonarr", success=True))
    for f in files:
        label = f.episode_label or f.path
        try:
            if os.path.lexists(f.path):
                os.remove(f.path)
            session.delete(f)
            steps.append(DeleteStepResult(kind="library_file", label=label, success=True))
        except OSError as exc:
            steps.append(DeleteStepResult(kind="library_file", label=label, success=False, error=str(exc)))


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
            all_file_count = len(session.exec(select(MediaFile.id).where(MediaFile.media_id == media.id)).all())
            whole_series = (
                selection.remove_from_arr
                and media.sonarr_id
                and settings.sonarr_url
                and settings.sonarr_api_key
                and len(files) == all_file_count
            )
            if whole_series:
                sonarr = SonarrClient(settings.sonarr_url, settings.sonarr_api_key)
                await _delete_whole_series(list(files), media, sonarr, steps, session)
            else:
                await _delete_episode_files(files, settings, selection.remove_from_arr, steps, session)

    session.commit()

    remaining_files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    remaining_torrents = session.exec(select(Torrent).where(Torrent.media_id == media.id)).all()

    if not remaining_files and not remaining_torrents:
        # Plus rien ne subsiste pour ce média : la fiche elle-même disparaît
        # (le cache média est de toute façon entièrement reconstruit à
        # chaque scan, voir database.py) plutôt que de rester affichée vide,
        # "manquant_emby" + "manquant_qbit" pour toujours jusqu'au prochain
        # scan complet.
        session.delete(media)
        session.commit()
        return MediaDeleteSelectionResult(steps=steps, media_deleted=True)

    # Recalcul immédiat : sans ça, la fiche resterait fausse (statuts et
    # espace récupérable) jusqu'au prochain scan complet.
    statuses, reclaimable = compute_statuses(list(remaining_files), list(remaining_torrents), bool(media.emby_item_id))
    media.statuses = ",".join(sorted(statuses))
    media.reclaimable_bytes = reclaimable
    session.add(media)
    session.commit()

    return MediaDeleteSelectionResult(steps=steps, media_deleted=False)
