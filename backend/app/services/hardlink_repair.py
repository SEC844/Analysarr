import os
import re

from sqlmodel import Session, select

from app.clients.qbittorrent import QbittorrentClient
from app.models.media import Media, MediaFile, Torrent
from app.models.settings import Settings
from app.schemas.media import (
    HardlinkRepairItem,
    HardlinkRepairPreview,
    HardlinkRepairResult,
    HardlinkRepairStepResult,
)
from app.services.hardlink import stat_inode

_EPISODE_PATTERN = re.compile(r"s(\d{1,2})e(\d{1,3})", re.IGNORECASE)


def _episode_label_from_filename(name: str) -> str | None:
    match = _EPISODE_PATTERN.search(name)
    if not match:
        return None
    return f"S{int(match.group(1)):02d}E{int(match.group(2)):02d}"


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
    current_files = session.exec(
        select(MediaFile).where(MediaFile.media_id == media.id, MediaFile.is_current == True)  # noqa: E712
    ).all()
    orphan_torrents = session.exec(
        select(Torrent).where(Torrent.media_id == media.id, Torrent.is_hardlinked == False)  # noqa: E712
    ).all()

    if not orphan_torrents or not current_files:
        return HardlinkRepairPreview(items=[], unmatched_torrents=[t.name for t in orphan_torrents])

    items: list[HardlinkRepairItem] = []
    matched_torrent_ids: set[int] = set()

    async with QbittorrentClient(settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password) as qbit:
        by_episode: dict[str, tuple[Torrent, str, int | None]] = {}
        single_file_by_torrent: dict[int, tuple[str, int | None]] = {}
        resolvable_torrents: set[int] = set()

        for t in orphan_torrents:
            torrent_files = await _resolve_torrent_files(qbit, t)
            existing = [(p, size) for p, size in torrent_files if os.path.isfile(p)]
            if not existing:
                continue
            resolvable_torrents.add(t.id)
            if len(existing) == 1:
                single_file_by_torrent[t.id] = existing[0]
            for path, size in existing:
                label = _episode_label_from_filename(os.path.basename(path))
                if label and label not in by_episode:
                    by_episode[label] = (t, path, size)

        for f in current_files:
            if f.episode_label:
                match = by_episode.get(f.episode_label)
                if not match:
                    continue
                torrent, torrent_path, size = match
            elif len(current_files) == 1 and len(orphan_torrents) == 1:
                # Film : un seul fichier actuel, un seul torrent orphelin candidat —
                # apparié seulement si ce torrent n'a qu'un seul fichier, pour ne
                # pas deviner lequel correspond parmi d'éventuels extras/bonus.
                single = single_file_by_torrent.get(orphan_torrents[0].id)
                if not single:
                    continue
                torrent = orphan_torrents[0]
                torrent_path, size = single
            else:
                continue

            current_inode = stat_inode(f.path)
            torrent_inode = stat_inode(torrent_path)
            if current_inode is not None and current_inode == torrent_inode:
                continue  # déjà hardlinké (sécurité, ne devrait pas arriver ici)

            items.append(
                HardlinkRepairItem(
                    media_file_id=f.id,
                    episode_label=f.episode_label,
                    current_path=f.path,
                    current_exists=current_inode is not None,
                    torrent_id=torrent.id,
                    torrent_name=torrent.name,
                    torrent_file_path=torrent_path,
                    size=size,
                )
            )
            matched_torrent_ids.add(torrent.id)

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
