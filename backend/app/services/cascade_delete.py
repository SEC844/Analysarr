
import httpx
from sqlmodel import Session, select

from app.clients.torrent import TorrentAuthError, torrent_client
from app.models.media import Media, MediaFile, Torrent
from app.models.settings import Settings
from app.schemas.media import (
    DeleteExecuteResult,
    DeletePreview,
    DeletePreviewItem,
    DeleteStepResult,
)
from app.services.media_delete import torrent_paths
from app.services.path_guard import ensure_paths_available
from app.services.scan import compute_statuses
from app.services.trash import close_action, delete_or_trash, open_action, trash_torrent


def _resolve_candidates(session: Session, media: Media) -> tuple[list[MediaFile], list[Torrent]]:
    """Fichiers en doublon "non actuels" à supprimer directement, et torrents orphelins
    à supprimer via qBittorrent. Utilisé identiquement par le preview et l'exécution."""
    files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    torrents = session.exec(select(Torrent).where(Torrent.media_id == media.id)).all()

    groups: dict[str | None, list[MediaFile]] = {}
    for f in files:
        groups.setdefault(f.episode_label, []).append(f)

    duplicate_files: list[MediaFile] = []
    for group_files in groups.values():
        if len(group_files) <= 1:
            continue
        resolved = [(f.inode, f.device) for f in group_files if f.inode is not None]
        confirmed_distinct = bool(resolved) and len(set(resolved)) > 1
        unverifiable = not resolved
        if not (confirmed_distinct or unverifiable):
            continue
        candidates = [f for f in group_files if not f.is_current]
        if not candidates:
            # Aucun fichier marqué "actuel" (série non trouvée dans l'historique Sonarr,
            # par exemple) : on garde le plus volumineux par précaution.
            candidates = sorted(group_files, key=lambda f: f.size or 0, reverse=True)[1:]
        duplicate_files.extend(candidates)

    # Un torrent "repairable" a le même contenu qu'un fichier actuellement
    # suivi par la bibliothèque (voir compute_statuses/_collect dans scan.py) :
    # ce n'est pas un vrai orphelin, le supprimer perdrait le fichier même que
    # "Réparer les hardlinks" propose d'utiliser pour protéger le média.
    orphan_torrents = [t for t in torrents if t.is_hardlinked is False and not t.repairable]

    return duplicate_files, orphan_torrents


def build_delete_preview(session: Session, media: Media) -> DeletePreview:
    duplicate_files, orphan_torrents = _resolve_candidates(session, media)

    items = [DeletePreviewItem(kind="duplicate_file", label=f.path, size=f.size) for f in duplicate_files]
    items += [DeletePreviewItem(kind="orphan_torrent", label=t.name, size=t.size) for t in orphan_torrents]

    total = sum(f.size or 0 for f in duplicate_files)
    # Plusieurs torrents orphelins peuvent être des copies cross-seed d'une
    # même ancienne version (même inode entre eux, même octet sur le disque) :
    # les supprimer tous ne libère l'espace qu'une seule fois, pas une fois
    # par torrent. Même logique que compute_statuses dans scan.py.
    seen_inodes: set[tuple[int, int]] = set()
    for t in orphan_torrents:
        key = (t.inode, t.device) if t.inode is not None else None
        if key is not None:
            if key in seen_inodes:
                continue
            seen_inodes.add(key)
        total += t.size or 0

    return DeletePreview(items=items, total_reclaimable_bytes=total)


async def execute_delete(session: Session, media: Media, settings: Settings) -> DeleteExecuteResult:
    duplicate_files, orphan_torrents = _resolve_candidates(session, media)
    # Voir services/path_guard.py : un volume non monté ferait passer des
    # fichiers intacts pour des doublons ou des orphelins supprimables.
    ensure_paths_available(settings, [f.path for f in duplicate_files], "Suppression")

    steps: list[DeleteStepResult] = []
    # Une seule action de corbeille pour tout le nettoyage : c'est aussi le
    # chemin qu'empruntent les automatisations, donc leur filet de sécurité.
    trash = open_action(session, settings, media, "cascade_delete")

    for f in duplicate_files:
        try:
            delete_or_trash(session, settings, f.path, action=trash, label=f.path)
            session.delete(f)
            steps.append(DeleteStepResult(kind="duplicate_file", label=f.path, success=True))
        except OSError as exc:
            steps.append(DeleteStepResult(kind="duplicate_file", label=f.path, success=False, error=str(exc)))

    if orphan_torrents:
        try:
            async with torrent_client(settings) as qbit:
                if trash is not None:
                    for t in orphan_torrents:
                        await trash_torrent(session, settings, qbit, trash, t, torrent_paths(session, t))
                else:
                    await qbit.delete_torrents([t.hash for t in orphan_torrents], delete_files=True)
            for t in orphan_torrents:
                session.delete(t)
                steps.append(DeleteStepResult(kind="orphan_torrent", label=t.name, success=True))
        except (TorrentAuthError, httpx.HTTPError, OSError, RuntimeError) as exc:
            for t in orphan_torrents:
                steps.append(DeleteStepResult(kind="orphan_torrent", label=t.name, success=False, error=str(exc)))

    close_action(session, trash)
    session.commit()

    # Le statut et l'espace récupérable affichés sont calculés au moment du scan : sans
    # ce recalcul, la fiche resterait "doublon"/"orphelin_qbit" jusqu'au prochain scan
    # complet alors que les éléments concernés viennent d'être supprimés.
    remaining_files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    remaining_torrents = session.exec(select(Torrent).where(Torrent.media_id == media.id)).all()
    statuses, reclaimable = compute_statuses(list(remaining_files), list(remaining_torrents), bool(media.emby_item_id))
    media.statuses = ",".join(sorted(statuses))
    media.reclaimable_bytes = reclaimable
    session.add(media)
    session.commit()

    return DeleteExecuteResult(steps=steps)
