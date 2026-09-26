
from sqlmodel import Session, select

from app.models.media import Media, MediaFile, Torrent
from app.models.settings import Settings
from app.schemas.media import (
    DeleteExecuteResult,
    DeletePreview,
    DeletePreviewItem,
    DeleteStepResult,
)
from app.services.deletion import DeletionFailed, DeletionTransaction, ensure_deletable
from app.services.path_guard import ensure_paths_available
from app.services.scan.statuses import compute_statuses, is_orphan


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
    # suivi par la bibliothèque (voir services/scan/statuses.py et torrent_match.py) :
    # ce n'est pas un vrai orphelin, le supprimer perdrait le fichier même que
    # "Réparer les hardlinks" propose d'utiliser pour protéger le média. Un
    # torrent « non importé » (saisons prises d'avance) n'est pas un orphelin.
    orphan_torrents = [t for t in torrents if is_orphan(t)]

    return duplicate_files, orphan_torrents


def build_delete_preview(session: Session, media: Media) -> DeletePreview:
    duplicate_files, orphan_torrents = _resolve_candidates(session, media)

    items = [DeletePreviewItem(kind="duplicate_file", label=f.path, size=f.size) for f in duplicate_files]
    items += [DeletePreviewItem(kind="orphan_torrent", label=t.name, size=t.size) for t in orphan_torrents]

    total = sum(f.size or 0 for f in duplicate_files)
    # Plusieurs torrents orphelins peuvent être des copies cross-seed d'une
    # même ancienne version (même inode entre eux, même octet sur le disque) :
    # les supprimer tous ne libère l'espace qu'une seule fois, pas une fois
    # par torrent. Même logique que services/scan/statuses.py.
    seen_inodes: set[tuple[int, int | None]] = set()
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

    # Droits vérifiés avant toute modification, comme pour la suppression
    # sélective : un nettoyage réussit en entier ou ne fait rien.
    ensure_deletable(session, settings, [f.path for f in duplicate_files], orphan_torrents, "Nettoyage")

    file_labels = [f.path for f in duplicate_files]
    torrent_labels = [t.name for t in orphan_torrents]
    # Une seule transaction pour tout le nettoyage : c'est aussi le chemin
    # qu'empruntent les automatisations, donc leur filet de sécurité.
    tx = DeletionTransaction(session, settings, media, "cascade_delete")
    try:
        for label in file_labels:
            tx.stage_file(label, label)
        await tx.stage_torrents(orphan_torrents)
        await tx.delete_unreachable_torrents()
    except DeletionFailed as failure:
        return DeleteExecuteResult(steps=await tx.rollback(failure))

    for f in duplicate_files:
        session.delete(f)
    for t in orphan_torrents:
        session.delete(t)
    tx.commit()
    steps = [DeleteStepResult(kind="duplicate_file", label=label, success=True) for label in file_labels]
    steps += [DeleteStepResult(kind="orphan_torrent", label=label, success=True) for label in torrent_labels]

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
