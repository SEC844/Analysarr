import os

import httpx
from sqlmodel import Session, select

from app.clients.qbittorrent import QbittorrentAuthError, QbittorrentClient
from app.models.media import Media, MediaFile, Torrent
from app.models.settings import Settings
from app.schemas.media import (
    CrossSeedSearchResult,
    DeleteExecuteResult,
    DeletePreview,
    DeletePreviewItem,
    DeleteStepResult,
)
from app.services.scan import compute_statuses


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

    orphan_torrents = [t for t in torrents if t.is_hardlinked is False]

    return duplicate_files, orphan_torrents


def build_delete_preview(session: Session, media: Media) -> DeletePreview:
    duplicate_files, orphan_torrents = _resolve_candidates(session, media)

    items = [DeletePreviewItem(kind="duplicate_file", label=f.path, size=f.size) for f in duplicate_files]
    items += [DeletePreviewItem(kind="orphan_torrent", label=t.name, size=t.size) for t in orphan_torrents]
    total = sum(item.size or 0 for item in items)

    return DeletePreview(items=items, total_reclaimable_bytes=total)


async def execute_delete(session: Session, media: Media, settings: Settings) -> DeleteExecuteResult:
    duplicate_files, orphan_torrents = _resolve_candidates(session, media)
    steps: list[DeleteStepResult] = []

    for f in duplicate_files:
        try:
            os.remove(f.path)
            session.delete(f)
            steps.append(DeleteStepResult(kind="duplicate_file", label=f.path, success=True))
        except OSError as exc:
            steps.append(DeleteStepResult(kind="duplicate_file", label=f.path, success=False, error=str(exc)))

    if orphan_torrents:
        try:
            async with QbittorrentClient(
                settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password
            ) as qbit:
                await qbit.delete_torrents([t.hash for t in orphan_torrents], delete_files=True)
            for t in orphan_torrents:
                session.delete(t)
                steps.append(DeleteStepResult(kind="orphan_torrent", label=t.name, success=True))
        except (QbittorrentAuthError, httpx.HTTPError) as exc:
            for t in orphan_torrents:
                steps.append(DeleteStepResult(kind="orphan_torrent", label=t.name, success=False, error=str(exc)))

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


async def trigger_cross_seed_search(
    settings: Settings, torrent_hashes: list[str], file_paths: list[str] | None = None
) -> CrossSeedSearchResult:
    """Déclenche une recherche cross-seed par infoHash pour chaque torrent connu.

    Si le média n'a AUCUN torrent en qBittorrent (statut manquant_qbit), on
    recherche à la place à partir du chemin de ses fichiers Emby (`path`) :
    l'API webhook de cross-seed accepte l'un ou l'autre. C'est ce qui permet
    de lancer une recherche même pour un média jamais seedé."""
    if not (settings.cross_seed_enabled and settings.cross_seed_url and settings.cross_seed_api_key):
        return CrossSeedSearchResult(triggered=0, errors=["cross-seed n'est pas activé ou configuré."])

    base = settings.cross_seed_url.rstrip("/")
    errors: list[str] = []
    triggered = 0

    targets: list[tuple[str, dict[str, str]]] = [(h, {"infoHash": h}) for h in torrent_hashes]
    if not targets:
        targets = [(p, {"path": p}) for p in file_paths or []]

    async with httpx.AsyncClient(timeout=15.0) as client:
        for label, body in targets:
            try:
                resp = await client.post(
                    f"{base}/api/webhook",
                    params={"apikey": settings.cross_seed_api_key},
                    data=body,
                )
                resp.raise_for_status()
                triggered += 1
            except httpx.HTTPStatusError as exc:
                # Le corps de la réponse de cross-seed explique précisément le refus
                # (ex : chemin hors de ses dataDirs configurés) — sans lui, l'erreur
                # httpx générique ("400 Bad Request") ne dit rien d'exploitable.
                detail = exc.response.text.strip()[:200] or exc.response.reason_phrase
                errors.append(f"{label} : HTTP {exc.response.status_code} — {detail}")
            except httpx.HTTPError as exc:
                errors.append(f"{label} : {exc}")

    return CrossSeedSearchResult(triggered=triggered, errors=errors)
