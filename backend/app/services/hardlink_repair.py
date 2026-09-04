import os

from sqlmodel import Session, select

from app.clients.qbittorrent import QbittorrentClient
from app.models.media import Media, MediaFile, MediaType, Torrent
from app.models.settings import Settings
from app.schemas.media import (
    HardlinkRepairItem,
    HardlinkRepairPreview,
    HardlinkRepairResult,
    HardlinkRepairStepResult,
)
from app.services.hardlink import episode_label_from_filename, resolve_current_files, stat_inode


async def _resolve_torrent_files(qbit: QbittorrentClient, torrent: Torrent) -> list[tuple[str, int | None]]:
    """[(chemin_absolu, taille)] pour chaque fichier réel du torrent — même
    logique que le scan (torrents/files + save_path), avec repli sur
    content_path si l'API n'a rien renvoyé."""
    try:
        files = await qbit.get_files(torrent.hash)
    except Exception:  # noqa: BLE001 - un échec ne doit pas bloquer le repli
        files = []
    resolved: list[tuple[str, int | None]] = []
    if files and torrent.save_path:
        for f in files:
            rel = f.get("name")
            if rel:
                resolved.append((os.path.join(torrent.save_path, rel), f.get("size")))
    elif torrent.content_path:
        resolved.append((torrent.content_path, torrent.size))
    return resolved


async def build_repair_preview(session: Session, media: Media, settings: Settings) -> HardlinkRepairPreview:
    """Pour chaque torrent orphelin (is_hardlinked=False) rattaché à ce média,
    tente d'apparier un de ses fichiers avec le fichier actuellement suivi par
    Emby/Sonarr/Radarr pour le même épisode (ou, pour un film, le fichier
    unique) — la paire que `execute_repair` remplacera par un hardlink."""
    all_files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    # Seuls les torrents "repairable" sont éligibles : leur contenu (même
    # épisode/média, même taille en octets) a déjà été vérifié identique à un
    # fichier actuellement suivi par la bibliothèque au moment du scan (voir
    # compute_statuses/_collect dans scan.py) — un vrai orphelin (contenu
    # différent, ex : ancienne qualité remplacée par un upgrade) n'est pas
    # réparable : le proposer ici remplacerait le bon fichier par le mauvais.
    orphan_torrents = session.exec(
        select(Torrent).where(
            Torrent.media_id == media.id,
            Torrent.is_hardlinked == False,  # noqa: E712
            Torrent.repairable == True,  # noqa: E712
        )
    ).all()

    by_episode, current_single = resolve_current_files(list(all_files), media.media_type)
    if not orphan_torrents or (not by_episode and current_single is None):
        return HardlinkRepairPreview(items=[], unmatched_torrents=[t.name for t in orphan_torrents])

    items: list[HardlinkRepairItem] = []
    matched_torrent_ids: set[int] = set()

    async with QbittorrentClient(settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password) as qbit:
        for t in orphan_torrents:
            torrent_files = await _resolve_torrent_files(qbit, t)
            existing = [(p, size) for p, size in torrent_files if os.path.isfile(p)]
            if not existing:
                continue

            for path, size in existing:
                if media.media_type == MediaType.series:
                    label = episode_label_from_filename(os.path.basename(path))
                    current = by_episode.get(label) if label else None
                else:
                    current = current_single

                # Même vérification de contenu qu'au scan (même taille en
                # octets) : une source différente entre-temps (torrent modifié,
                # fichier remplacé) ne doit pas produire une réparation erronée.
                if current is None or size is None or current.size != size:
                    continue

                current_inode = stat_inode(current.path)
                torrent_inode = stat_inode(path)
                if current_inode is not None and current_inode == torrent_inode:
                    continue  # déjà hardlinké (sécurité, ne devrait pas arriver ici)

                items.append(
                    HardlinkRepairItem(
                        media_file_id=current.id,
                        episode_label=current.episode_label,
                        current_path=current.path,
                        current_exists=current_inode is not None,
                        torrent_id=t.id,
                        torrent_name=t.name,
                        torrent_file_path=path,
                        size=size,
                    )
                )
                matched_torrent_ids.add(t.id)
                if media.media_type == MediaType.movie:
                    break  # un seul fichier actuel pour un film, inutile de continuer

    unmatched = [t.name for t in orphan_torrents if t.id not in matched_torrent_ids]
    return HardlinkRepairPreview(items=items, unmatched_torrents=unmatched)


async def execute_repair(session: Session, media: Media, settings: Settings) -> HardlinkRepairResult:
    """Pour chaque paire identifiée par `build_repair_preview` : supprime le
    fichier actuellement suivi par la bibliothèque (une copie séparée, non
    protégée) et le remplace par un hardlink vers le fichier du torrent —
    qui, lui, est réellement seedé. Après coup, le torrent protège enfin le
    fichier que la bibliothèque sert."""
    preview = await build_repair_preview(session, media, settings)

    steps: list[HardlinkRepairStepResult] = []
    for item in preview.items:
        label = item.episode_label or item.current_path
        try:
            if os.path.exists(item.current_path):
                os.remove(item.current_path)
            os.link(item.torrent_file_path, item.current_path)
            steps.append(HardlinkRepairStepResult(media_file_id=item.media_file_id, label=label, success=True))
        except OSError as exc:
            steps.append(
                HardlinkRepairStepResult(media_file_id=item.media_file_id, label=label, success=False, error=str(exc))
            )

    # Recalcul immédiat : les fichiers réparés partagent maintenant l'inode du
    # torrent, donc plus orphelin_qbit/manquant_qbit pour eux — sans ce
    # recalcul la fiche resterait fausse jusqu'au prochain scan complet.
    from app.services.scan import compute_statuses  # import différé : évite un cycle avec scan.py

    repaired_ids = {s.media_file_id for s in steps if s.success}
    repaired_torrent_ids = {i.torrent_id for i in preview.items if i.media_file_id in repaired_ids}

    all_files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    all_torrents = session.exec(select(Torrent).where(Torrent.media_id == media.id)).all()

    for f in all_files:
        if f.id in repaired_ids:
            inode = stat_inode(f.path)
            f.inode, f.device = (inode[0], inode[1]) if inode else (None, None)
            session.add(f)
    for t in all_torrents:
        if t.id in repaired_torrent_ids:
            t.is_hardlinked = True
            session.add(t)

    statuses, reclaimable = compute_statuses(list(all_files), list(all_torrents), bool(media.emby_item_id))
    media.statuses = ",".join(sorted(statuses))
    media.reclaimable_bytes = reclaimable
    session.add(media)
    session.commit()

    return HardlinkRepairResult(steps=steps)
