"""Suppression manuelle et sélective d'un média : contrairement à
cascade_delete.py (qui n'agit que sur les doublons/orphelins détectés
automatiquement), c'est ici l'utilisateur qui choisit lui-même quels
torrents et/ou quels fichiers de bibliothèque supprimer — un épisode, une
saison entière, toute la série, ou le film — avec la possibilité d'arrêter
aussi le suivi Sonarr/Radarr pour éviter un retéléchargement automatique."""

import os
import stat as stat_module

import httpx
from sqlmodel import Session, delete, select

from app.clients.torrent import TorrentAuthError, torrent_client, torrent_client_configured
from app.models.media import Media, MediaFile, MediaRequest, MediaType, MediaWatch, Torrent, TorrentFile
from app.models.settings import Settings
from app.models.trash import TrashAction
from app.schemas.media import (
    DeleteFootprintItem,
    DeleteStepResult,
    DiskUnit,
    MediaDeleteFootprint,
    MediaDeleteSelection,
    MediaDeleteSelectionResult,
)
from app.services.arr_instances import ArrTarget, arr_target_for
from app.services.path_guard import ensure_paths_available
from app.services.trash import capture_arr, capture_seer, close_action, delete_or_trash, open_action, trash_torrent
from app.services.hardlink import resolve_torrent_files
from app.services.scan import compute_statuses, current_files_size
from app.services.seer import remove_seer_requests


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
    if torrents and torrent_client_configured(settings):
        try:
            async with torrent_client(settings) as qbit:
                for t in torrents:
                    torrent_files[t.id] = await resolve_torrent_files(qbit, t)
        except (TorrentAuthError, httpx.HTTPError):
            torrent_files.clear()  # repli ci-dessous sur content_path

    torrent_items: list[DeleteFootprintItem] = []
    for t in torrents:
        paths = torrent_files.get(t.id) or await resolve_torrent_files(None, t)
        unit_ids = [i for path, size in paths for i in unit_for(path, size)] if paths else unit_for(None, t.size)
        torrent_items.append(DeleteFootprintItem(id=t.id, units=unit_ids))

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


def torrent_paths(session: Session, torrent: Torrent) -> list[str]:
    """Éléments à déplacer pour mettre les données du torrent de côté :
    `content_path` (le fichier lui-même, ou le dossier racine d'un torrent
    multi-fichiers), sinon les chemins mémorisés au dernier scan."""
    if torrent.content_path:
        return [torrent.content_path]
    rows = session.exec(select(TorrentFile.path).where(TorrentFile.torrent_hash == torrent.hash)).all()
    return [path for path in rows if path]


async def _delete_torrents(
    torrents: list[Torrent],
    settings: Settings,
    steps: list[DeleteStepResult],
    session: Session,
    action: TrashAction | None = None,
) -> None:
    """Supprime les torrents ET leurs données. Avec la corbeille : chaque
    torrent est retiré du client SANS toucher aux fichiers, qui sont déplacés
    de côté — c'est ce qui permet de le remettre en seed à l'identique (voir
    services/trash.py)."""
    if not torrents:
        return
    try:
        async with torrent_client(settings) as qbit:
            if action is not None:
                for t in torrents:
                    await trash_torrent(session, settings, qbit, action, t, torrent_paths(session, t))
            else:
                await qbit.delete_torrents([t.hash for t in torrents], delete_files=True)
        for t in torrents:
            session.delete(t)
            steps.append(DeleteStepResult(kind="torrent", label=t.name, success=True))
    except (TorrentAuthError, httpx.HTTPError, OSError, RuntimeError) as exc:
        for t in torrents:
            steps.append(DeleteStepResult(kind="torrent", label=t.name, success=False, error=str(exc)))


def _already_gone(exc: Exception) -> bool:
    """404 de Sonarr/Radarr : le fichier n'existe déjà plus de leur côté (série
    retirée entre-temps, suppression rejouée). C'est le résultat voulu, pas un
    échec — bug réel : une seconde tentative affichait deux erreurs 404 alors
    qu'il n'y avait plus rien à supprimer."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404


async def _delete_movie_files(
    files: list[MediaFile],
    target: ArrTarget | None,
    steps: list[DeleteStepResult],
    session: Session,
    settings: Settings | None = None,
    action: TrashAction | None = None,
) -> None:
    radarr = target.radarr() if target is not None else None
    for f in files:
        # Libellés lus AVANT la suppression : une fois la ligne retirée de la
        # session, ses attributs ne sont plus lisibles.
        label = f.path
        try:
            if f.arr_file_id and radarr is not None:
                await radarr.delete_movie_file(f.arr_file_id)
            else:
                # Fichier en trop jamais suivi par Radarr (doublon) : rien à
                # supprimer côté Radarr, juste le fichier lui-même.
                delete_or_trash(session, settings, label, action=action, label=label)
            session.delete(f)
            steps.append(DeleteStepResult(kind="library_file", label=label, success=True))
        except (httpx.HTTPError, OSError) as exc:
            if _already_gone(exc):
                session.delete(f)
                steps.append(DeleteStepResult(kind="library_file", label=label, success=True))
                continue
            steps.append(DeleteStepResult(kind="library_file", label=label, success=False, error=str(exc)))


async def _remove_media_from_arr(
    files: list[MediaFile],
    media: Media,
    target: ArrTarget | None,
    steps: list[DeleteStepResult],
    session: Session,
    settings: Settings | None = None,
    action: TrashAction | None = None,
) -> bool:
    """Toute la bibliothèque du média sélectionnée avec retrait Sonarr/Radarr :
    un seul appel au niveau du MÉDIA (film ou série + dossier, sans liste
    d'exclusion), puis suppression directe des fichiers qui subsisteraient.

    Ne dépend volontairement PAS de `MediaFile.arr_file_id` : il reste vide
    pour un fichier que le scan n'a pas pu rapprocher de Sonarr/Radarr (voir
    scan._file_match_strength) — le film/la série resterait alors dans
    Radarr/Sonarr alors que ses fichiers seraient supprimés.

    `target` : instance qui suit CE média (voir services/arr_instances.py).
    Renvoie False si elle n'est pas configurée (ou a été supprimée) ou si le
    média lui est inconnu (repli sur la suppression fichier par fichier)."""
    if target is None:
        return False
    if media.media_type == MediaType.movie:
        if not media.radarr_id:
            return False
        remove = target.radarr().delete_movie(media.radarr_id)
    else:
        if not media.sonarr_id:
            return False
        remove = target.sonarr().delete_series(media.sonarr_id)
    service = target.name

    try:
        await remove
    except httpx.HTTPError as exc:
        # Rien n'est supprimé du disque si Sonarr/Radarr refuse : l'utilisateur
        # voit l'erreur et peut réessayer sans avoir perdu ses fichiers.
        steps.append(DeleteStepResult(kind="arr_media", label=media.title, success=False, error=str(exc)))
        return True
    steps.append(DeleteStepResult(kind="arr_media", label=f"{media.title} retiré de {service}", success=True))
    for f in files:
        label = f.episode_label or f.path
        path = f.path
        try:
            if os.path.lexists(path):
                delete_or_trash(session, settings, path, action=action, label=label)
            session.delete(f)
            steps.append(DeleteStepResult(kind="library_file", label=label, success=True))
        except OSError as exc:
            steps.append(DeleteStepResult(kind="library_file", label=label, success=False, error=str(exc)))
    return True


async def _delete_episode_files(
    files: list[MediaFile],
    target: ArrTarget | None,
    remove_from_arr: bool,
    steps: list[DeleteStepResult],
    session: Session,
    settings: Settings | None = None,
    action: TrashAction | None = None,
) -> None:
    sonarr = target.sonarr() if target is not None else None
    episodes_to_unmonitor: list[int] = []
    for f in files:
        label = f.episode_label or f.path
        path, episode_id, file_id = f.path, f.sonarr_episode_id, f.arr_file_id
        try:
            if file_id and sonarr is not None:
                await sonarr.delete_episode_file(file_id)
                if remove_from_arr and episode_id:
                    episodes_to_unmonitor.append(episode_id)
            else:
                delete_or_trash(session, settings, path, action=action, label=label)
            session.delete(f)
            steps.append(DeleteStepResult(kind="library_file", label=label, success=True))
        except (httpx.HTTPError, OSError) as exc:
            if _already_gone(exc):
                session.delete(f)
                steps.append(DeleteStepResult(kind="library_file", label=label, success=True))
                continue
            steps.append(DeleteStepResult(kind="library_file", label=label, success=False, error=str(exc)))

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


async def _capture_arr_media(session: Session, media: Media, target: ArrTarget | None, trash: TrashAction) -> None:
    """Lit la fiche du média chez Sonarr/Radarr pour la mettre de côté. Un
    échec n'empêche pas la suppression : la corbeille restaurera alors les
    fichiers sans le suivi."""
    if target is None:
        return
    try:
        if media.media_type == MediaType.movie and media.radarr_id:
            body = await target.radarr().get_movie(media.radarr_id)
        elif media.media_type != MediaType.movie and media.sonarr_id:
            body = await target.sonarr().get_series_by_id(media.sonarr_id)
        else:
            return
    except httpx.HTTPError:
        return
    service = "radarr" if media.media_type == MediaType.movie else "sonarr"
    capture_arr(session, trash, service, media.arr_instance_id, body)


async def capture_seer_requests(session: Session, media: Media, settings: Settings, trash: TrashAction) -> None:
    """Demandes Seer complètes, capturées avant leur suppression : elles sont
    recréées telles quelles (même demandeur, mêmes saisons) à la restauration."""
    from app.clients.seer import SeerClient
    from app.models.media import MediaRequest
    from app.services.seer import seer_configured

    rows = list(session.exec(select(MediaRequest).where(MediaRequest.media_id == media.id)).all())
    if not rows or not seer_configured(settings):
        return
    client = SeerClient(settings.seer_url, settings.seer_api_key)
    captured = []
    for row in rows:
        try:
            request = await client.get_request(row.seer_request_id)
        except httpx.HTTPError:
            continue
        if request:
            captured.append(request)
    capture_seer(session, trash, captured)


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

    # Montages vérifiés AVANT la première suppression : un volume démonté
    # ferait disparaître des fichiers bien vivants de la base, de Sonarr/Radarr
    # et de la fiche média (voir services/path_guard.py).
    if files or selection.remove_from_arr:
        ensure_paths_available(settings, [f.path for f in files], "Suppression")

    steps: list[DeleteStepResult] = []
    target = arr_target_for(session, settings, media)
    # Corbeille : tout ce qui part dans cette suppression est regroupé dans UNE
    # action, restaurable d'un bloc (voir services/trash.py).
    trash = open_action(session, settings, media, "delete_selection")
    await _delete_torrents(torrents, settings, steps, session, trash)

    all_file_count = len(session.exec(select(MediaFile.id).where(MediaFile.media_id == media.id)).all())
    # Retrait du média entier de Sonarr/Radarr uniquement si TOUTE sa
    # bibliothèque est sélectionnée (y compris aucune, pour un média sans
    # fichier) : sinon on supprimerait des fichiers non cochés.
    whole_library = selection.remove_from_arr and len(files) == all_file_count
    if whole_library and trash is not None:
        # Fiche Sonarr/Radarr capturée AVANT le retrait : c'est elle qui permet
        # de recréer le film ou la série à la restauration.
        await _capture_arr_media(session, media, target, trash)
    removed_from_arr = whole_library and await _remove_media_from_arr(
        list(files), media, target, steps, session, settings, trash
    )
    if files and not removed_from_arr:
        if media.media_type == MediaType.movie:
            await _delete_movie_files(files, target, steps, session, settings, trash)
        else:
            await _delete_episode_files(files, target, selection.remove_from_arr, steps, session, settings, trash)

    # Demande Seer : seulement si toute la bibliothèque du média a bien été
    # supprimée — jamais pour un média qui existe encore, même en partie.
    library_failed = any(not s.success for s in steps if s.kind in ("library_file", "arr_media"))
    if selection.remove_from_seer and len(files) == all_file_count and not library_failed:
        if trash is not None:
            await capture_seer_requests(session, media, settings, trash)
        await remove_seer_requests(session, media, settings, steps)

    close_action(session, trash)
    session.commit()

    remaining_files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    remaining_torrents = session.exec(select(Torrent).where(Torrent.media_id == media.id)).all()

    if not remaining_files and not remaining_torrents:
        # Plus rien ne subsiste pour ce média : la fiche elle-même disparaît
        # (le cache média est de toute façon entièrement reconstruit à
        # chaque scan, voir database.py) plutôt que de rester affichée vide,
        # "manquant_emby" + "manquant_qbit" pour toujours jusqu'au prochain
        # scan complet.
        session.exec(delete(MediaWatch).where(MediaWatch.media_id == media.id))
        session.exec(delete(MediaRequest).where(MediaRequest.media_id == media.id))
        session.delete(media)
        session.commit()
        return MediaDeleteSelectionResult(steps=steps, media_deleted=True)

    # Recalcul immédiat : sans ça, la fiche resterait fausse (statuts et
    # espace récupérable) jusqu'au prochain scan complet.
    statuses, reclaimable = compute_statuses(list(remaining_files), list(remaining_torrents), bool(media.emby_item_id))
    media.statuses = ",".join(sorted(statuses))
    media.reclaimable_bytes = reclaimable
    media.total_size = current_files_size(list(remaining_files))
    session.add(media)
    session.commit()

    return MediaDeleteSelectionResult(steps=steps, media_deleted=False)
