"""Suppression manuelle et sélective d'un média : contrairement à
cascade_delete.py (qui n'agit que sur les doublons/orphelins détectés
automatiquement), c'est ici l'utilisateur qui choisit lui-même quels
torrents et/ou quels fichiers de bibliothèque supprimer — un épisode, une
saison entière, toute la série, ou le film — avec la possibilité d'arrêter
aussi le suivi Sonarr/Radarr pour éviter un retéléchargement automatique."""

import logging
import os
import stat as stat_module
from collections.abc import Sequence

import httpx
from sqlmodel import Session, col, delete, select

from app.clients.torrent import TorrentAuthError, torrent_client, torrent_client_configured
from app.models.ids import row_id
from app.models.media import Media, MediaFile, MediaRequest, MediaType, MediaWatch, Torrent
from app.models.settings import Settings
from app.schemas.media import (
    DeleteFootprintItem,
    DeleteStepResult,
    DiskUnit,
    MediaDeleteFootprint,
    MediaDeleteSelection,
    MediaDeleteSelectionResult,
)
from app.services.arr_instances import ArrTarget, arr_target_for
from app.services.deletion import DeletionFailed, DeletionTransaction, ensure_deletable
from app.services.hardlink import resolve_torrent_files
from app.services.path_guard import ensure_paths_available
from app.services.scan.statuses import compute_statuses, current_files_size

logger = logging.getLogger(__name__)


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
            # Chemin non résolu : estimation prudente ci-dessous.
            logger.debug("Fichier illisible pour l'empreinte disque : %s", path, exc_info=True)
            st = None
        if st is None or not stat_module.S_ISREG(st.st_mode):
            units.append(DiskUnit(size=fallback_size or 0, links=1))
            return [len(units) - 1]
        key = (st.st_ino, st.st_dev)
        if key not in unit_by_inode:
            unit_by_inode[key] = len(units)
            units.append(DiskUnit(size=st.st_size, links=st.st_nlink))
        return [unit_by_inode[key]]

    file_items = [DeleteFootprintItem(id=row_id(f), units=unit_for(f.path, f.size)) for f in files]

    torrent_files: dict[int, list[tuple[str, int | None]]] = {}
    if torrents and torrent_client_configured(settings):
        try:
            async with torrent_client(settings) as qbit:
                for t in torrents:
                    torrent_files[row_id(t)] = await resolve_torrent_files(qbit, t)
        except (TorrentAuthError, httpx.HTTPError):
            # Repli ci-dessous sur content_path : l'espace libéré devient une estimation.
            logger.warning("Fichiers des torrents illisibles pour l'empreinte disque", exc_info=True)
            torrent_files.clear()

    torrent_items: list[DeleteFootprintItem] = []
    for t in torrents:
        paths = torrent_files.get(row_id(t)) or await resolve_torrent_files(None, t)
        unit_ids = [i for path, size in paths for i in unit_for(path, size)] if paths else unit_for(None, t.size)
        torrent_items.append(DeleteFootprintItem(id=row_id(t), units=unit_ids))

    return MediaDeleteFootprint(units=units, torrents=torrent_items, files=file_items)


def reclaimed_bytes(footprint: MediaDeleteFootprint, torrent_ids: list[int], media_file_ids: list[int]) -> int:
    """Espace réellement libéré par une sélection — même calcul que le
    frontend (lib/footprint.ts) : une unité disque ne compte que si autant de
    ses liens sont sélectionnés qu'elle en a (`links`)."""
    selected_links: dict[int, int] = {}
    for items, ids in ((footprint.torrents, set(torrent_ids)), (footprint.files, set(media_file_ids))):
        for item in items:
            if item.id in ids:
                for unit in item.units:
                    selected_links[unit] = selected_links.get(unit, 0) + 1
    return sum(footprint.units[i].size for i, count in selected_links.items() if count >= footprint.units[i].links)


def _already_gone(exc: Exception) -> bool:
    """404 de Sonarr/Radarr : l'élément n'existe déjà plus de leur côté (série
    retirée entre-temps, suppression rejouée). C'est le résultat voulu, pas un
    échec — bug réel : une seconde tentative affichait deux erreurs 404 alors
    qu'il n'y avait plus rien à supprimer."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404


def _arr_failure(label: str, exc: Exception) -> DeletionFailed:
    return DeletionFailed(DeleteStepResult(kind="arr_media", label=label, success=False, error=str(exc)))


async def _remove_media_from_arr(tx: DeletionTransaction, media: Media, target: ArrTarget, arr_id: int) -> str:
    """Toute la bibliothèque du média sélectionnée avec retrait Sonarr/Radarr :
    un seul appel au niveau du MÉDIA, sans liste d'exclusion et sans que
    Sonarr/Radarr ne touche aux fichiers (`deleteFiles=false`) — ils sont déjà
    mis de côté par la transaction.

    Ne dépend volontairement PAS de `MediaFile.arr_file_id` : il reste vide
    pour un fichier que le scan n'a pas pu rapprocher de Sonarr/Radarr (voir
    scan._file_match_strength) — le film/la série resterait alors dans
    Radarr/Sonarr alors que ses fichiers seraient supprimés."""
    try:
        if media.media_type == MediaType.movie:
            await target.radarr().delete_movie(arr_id, delete_files=False)
        else:
            await target.sonarr().delete_series(arr_id, delete_files=False)
    except httpx.HTTPError as exc:
        if not _already_gone(exc):
            raise _arr_failure(media.title, exc) from exc
    tx.arr_media_removed()
    return f"{media.title} retiré de {target.name}"


async def _forget_movie_files(
    tx: DeletionTransaction, files: Sequence[MediaFile], target: ArrTarget, movie_id: int
) -> None:
    """Fichiers déjà mis de côté : Radarr oublie leurs enregistrements. Un
    fichier en trop jamais suivi par Radarr (doublon) n'a rien à oublier."""
    radarr = target.radarr()
    tracked = [(f.path, f.arr_file_id) for f in files if f.arr_file_id is not None]
    if tracked:
        tx.on_rollback(lambda: radarr.rescan_movie(movie_id))
    for path, file_id in tracked:
        try:
            await radarr.delete_movie_file(file_id)
        except httpx.HTTPError as exc:
            if not _already_gone(exc):
                raise _arr_failure(path, exc) from exc


async def _forget_episode_files(
    tx: DeletionTransaction, files: Sequence[MediaFile], target: ArrTarget, series_id: int, unmonitor: bool
) -> None:
    """Même principe pour Sonarr, qui n'a pas d'équivalent au retrait d'un
    film à la granularité épisode/saison : ses épisodes sont démonitorés en
    masse, ce qui empêche un retéléchargement automatique sans retirer la
    série."""
    sonarr = target.sonarr()
    tracked = [
        (f.episode_label or f.path, f.arr_file_id, f.sonarr_episode_id) for f in files if f.arr_file_id is not None
    ]
    if tracked:
        tx.on_rollback(lambda: sonarr.rescan_series(series_id))
    for label, file_id, _episode in tracked:
        try:
            await sonarr.delete_episode_file(file_id)
        except httpx.HTTPError as exc:
            if not _already_gone(exc):
                raise _arr_failure(label, exc) from exc
    episodes = [episode for _label, _file, episode in tracked if episode is not None] if unmonitor else []
    if not episodes:
        return
    try:
        await sonarr.set_episodes_monitored(episodes, monitored=False)
    except httpx.HTTPError as exc:
        raise _arr_failure(f"{len(episodes)} épisode(s) à démonitorer", exc) from exc
    tx.on_rollback(lambda: sonarr.set_episodes_monitored(episodes, monitored=True))


async def _update_arr(
    tx: DeletionTransaction,
    media: Media,
    target: ArrTarget | None,
    files: Sequence[MediaFile],
    selection: MediaDeleteSelection,
    whole_library: bool,
) -> str | None:
    """Sonarr/Radarr, après la mise de côté des fichiers. Renvoie le libellé
    du retrait du média, s'il a eu lieu."""
    arr_id = media.radarr_id if media.media_type == MediaType.movie else media.sonarr_id
    if target is None or not arr_id:
        return None  # média suivi par aucun Sonarr/Radarr, ou instance supprimée
    if whole_library:
        return await _remove_media_from_arr(tx, media, target, arr_id)
    if media.media_type == MediaType.movie:
        await _forget_movie_files(tx, files, target, arr_id)
    else:
        await _forget_episode_files(tx, files, target, arr_id, selection.remove_from_arr)
    return None


async def _capture(tx: DeletionTransaction, media: Media, target: ArrTarget) -> None:
    if media.media_type == MediaType.movie and media.radarr_id:
        movie_id = media.radarr_id
        await tx.capture_arr("radarr", media.arr_instance_id, lambda: target.radarr().get_movie(movie_id))
    elif media.media_type != MediaType.movie and media.sonarr_id:
        series_id = media.sonarr_id
        await tx.capture_arr("sonarr", media.arr_instance_id, lambda: target.sonarr().get_series_by_id(series_id))


def _selected(
    session: Session, media: Media, selection: MediaDeleteSelection
) -> tuple[Sequence[Torrent], Sequence[MediaFile]]:
    """Torrents et fichiers cochés, limités à CE média : un id d'un autre
    média est ignoré."""
    torrents = (
        session.exec(
            select(Torrent).where(col(Torrent.media_id) == media.id, col(Torrent.id).in_(selection.torrent_ids))
        ).all()
        if selection.torrent_ids
        else []
    )
    files = (
        session.exec(
            select(MediaFile).where(
                col(MediaFile.media_id) == media.id, col(MediaFile.id).in_(selection.media_file_ids)
            )
        ).all()
        if selection.media_file_ids
        else []
    )
    return torrents, files


async def execute_media_delete(
    session: Session, media: Media, settings: Settings, selection: MediaDeleteSelection
) -> MediaDeleteSelectionResult:
    torrents, files = _selected(session, media, selection)

    all_file_count = len(session.exec(select(MediaFile.id).where(MediaFile.media_id == media.id)).all())
    # Retrait du média entier de Sonarr/Radarr uniquement si TOUTE sa
    # bibliothèque est sélectionnée (y compris aucune, pour un média sans
    # fichier) : sinon on supprimerait des fichiers non cochés.
    whole_library = selection.remove_from_arr and len(files) == all_file_count
    target = arr_target_for(session, settings, media)

    # Tout est vérifié AVANT la première modification : un volume démonté
    # ferait disparaître des fichiers bien vivants (services/path_guard.py), et
    # un fichier impossible à déplacer laisserait la suppression à moitié faite.
    if files or selection.remove_from_arr:
        ensure_paths_available(settings, [f.path for f in files], "Suppression")
    ensure_deletable(session, settings, [f.path for f in files], torrents, "Suppression")

    # Libellés lus AVANT la suppression : une fois les lignes retirées de la
    # session, leurs attributs ne sont plus lisibles.
    file_labels = [f.episode_label or f.path for f in files]
    torrent_labels = [t.name for t in torrents]

    # Fichiers, puis Sonarr/Radarr, puis torrents : les étapes les plus faciles
    # à annuler d'abord (voir services/deletion.py).
    tx = DeletionTransaction(session, settings, media, "delete_selection")
    try:
        if whole_library and target is not None:
            await _capture(tx, media, target)
        for f, label in zip(files, file_labels, strict=True):
            tx.stage_file(f.path, label)
        removed = await _update_arr(tx, media, target, files, selection, whole_library)
        await tx.stage_torrents(torrents)
        await tx.delete_unreachable_torrents()
    except DeletionFailed as failure:
        return MediaDeleteSelectionResult(steps=await tx.rollback(failure), media_deleted=False)

    for f in files:
        session.delete(f)
    for t in torrents:
        session.delete(t)
    tx.commit()

    steps = [DeleteStepResult(kind="torrent", label=label, success=True) for label in torrent_labels]
    if removed:
        steps.append(DeleteStepResult(kind="arr_media", label=removed, success=True))
    steps += [DeleteStepResult(kind="library_file", label=label, success=True) for label in file_labels]

    return MediaDeleteSelectionResult(steps=steps, media_deleted=_refresh_media(session, media))


def _refresh_media(session: Session, media: Media) -> bool:
    """Fiche remise à jour après la suppression. Renvoie True si elle a
    disparu, faute de fichier et de torrent."""
    remaining_files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    remaining_torrents = session.exec(select(Torrent).where(Torrent.media_id == media.id)).all()

    if not remaining_files and not remaining_torrents:
        # Plus rien ne subsiste pour ce média : la fiche elle-même disparaît
        # (le cache média est de toute façon entièrement reconstruit à
        # chaque scan, voir database.py) plutôt que de rester affichée vide,
        # "manquant_emby" + "manquant_qbit" pour toujours jusqu'au prochain
        # scan complet.
        session.exec(delete(MediaWatch).where(col(MediaWatch.media_id) == media.id))
        session.exec(delete(MediaRequest).where(col(MediaRequest.media_id) == media.id))
        session.delete(media)
        session.commit()
        return True

    # Recalcul immédiat : sans ça, la fiche resterait fausse (statuts et
    # espace récupérable) jusqu'au prochain scan complet.
    statuses, reclaimable = compute_statuses(list(remaining_files), list(remaining_torrents), bool(media.emby_item_id))
    media.statuses = ",".join(sorted(statuses))
    media.reclaimable_bytes = reclaimable
    media.total_size = current_files_size(list(remaining_files))
    session.add(media)
    session.commit()
    return False
