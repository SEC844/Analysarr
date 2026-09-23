"""Corbeille : une suppression devient annulable pendant quelques jours.

Une suppression n'est pas une liste de fichiers, c'est un ENSEMBLE : des
fichiers de bibliothèque, des torrents, le suivi Sonarr/Radarr, la demande
Seer. La corbeille garde donc une ligne par ACTION, et la restauration remet
tout d'un coup — restaurer la moitié d'une suppression laisserait une
bibliothèque incohérente (fichiers présents mais plus suivis, torrent sans
données, média demandable deux fois).

Ce qui est réellement restauré :
- fichiers de bibliothèque et données de torrent : déplacés (`os.replace`,
  jamais copiés), donc mêmes inodes — les hardlinks survivent à l'aller comme
  au retour ;
- torrents : retirés du client SANS supprimer leurs fichiers, puis ré-ajoutés
  au même emplacement (fichier .torrent quand le client sait l'exporter, sinon
  lien magnet construit depuis les trackers), le client revérifie les données
  en place et reprend le seed sans rien retélécharger ;
- film/série : recréé dans Sonarr/Radarr à partir de la fiche capturée avant
  suppression (profil, dossier racine, tags, monitoring), puis rescan pour
  qu'il retrouve ses fichiers.

Seer reste à l'écart : son API ne sait pas recréer une demande à l'identique
(ni date d'origine, ni statut « disponible »), donc Analysarr ne supprime plus
et ne restaure plus rien côté Seer.

Ce qui ne peut PAS être restauré, aucune API de client torrent ne l'expose :
les statistiques de seed (ratio, quantité envoyée) repartent de zéro. Le
torrent reprend son seed avec ses données, mais son historique de partage est
perdu — c'est dit tel quel dans l'interface."""

import base64
import contextlib
import json
import logging
import os
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlmodel import Session, col, select

from app.clients.torrent import torrent_client, torrent_client_configured
from app.clients.torrent_base import TorrentAuthError, TorrentClient, magnet_for
from app.models.ids import row_id
from app.models.media import Media, Torrent
from app.models.settings import Settings
from app.models.trash import TrashAction, TrashItem
from app.schemas.media import DeleteStepResult
from app.services.arr_instances import arr_target_by_id
from app.services.companion_files import (
    companions,
    folders_of,
    has_video,
    is_inside,
    leftovers,
    prune_empty_dirs,
)

logger = logging.getLogger(__name__)

TRASH_DIR_NAME = ".analysarr-trash"
DEFAULT_RETENTION_DAYS = 7
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 90


def is_enabled(settings: Settings | None) -> bool:
    return bool(settings and settings.trash_enabled)


def retention_days(settings: Settings | None) -> int:
    value = settings.trash_retention_days if settings else DEFAULT_RETENTION_DAYS
    return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, value))


# --- Déplacement des fichiers -------------------------------------------------


def _unique_destination(directory: str, name: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    candidate = os.path.join(directory, f"{stamp}-{name}")
    index = 1
    while os.path.lexists(candidate):
        candidate = os.path.join(directory, f"{stamp}-{index}-{name}")
        index += 1
    return candidate


def _move_into(path: str, trash_dir: str) -> str:
    os.makedirs(trash_dir, exist_ok=True)
    destination = _unique_destination(trash_dir, os.path.basename(path.rstrip("/\\")))
    os.replace(path, destination)
    return destination


def move_to_trash(settings: Settings | None, path: str) -> str:
    """Déplace un fichier ou un dossier vers la corbeille et renvoie son
    nouveau chemin. La corbeille de la racine de la bibliothèque d'abord (même
    système de fichiers, donc déplacement instantané et inodes conservés),
    sinon un dossier `.analysarr-trash` à côté de l'élément — indispensable
    pour les téléchargements, souvent sur un autre volume."""
    root = (settings.emby_library_path or "").strip() if settings else ""
    if root:
        try:
            return _move_into(path, os.path.join(root, TRASH_DIR_NAME))
        except OSError as exc:
            if exc.errno != 18:  # EXDEV : autre système de fichiers
                raise
    return _move_into(path, os.path.join(os.path.dirname(path.rstrip("/\\")), TRASH_DIR_NAME))


def _restore_path(trashed_path: str, original_path: str) -> None:
    if os.path.lexists(original_path):
        raise FileExistsError(f"Un élément occupe déjà {original_path}.")
    os.makedirs(os.path.dirname(original_path), exist_ok=True)
    os.replace(trashed_path, original_path)


def _remove(path: str) -> None:
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except FileNotFoundError:
        # Déjà retiré à la main : l'objectif est atteint.
        logger.debug("Déjà absent de la corbeille : %s", path)


def _size_of(path: str) -> int:
    try:
        if not os.path.isdir(path) or os.path.islink(path):
            return os.stat(path).st_size
    except OSError:
        return 0
    total = 0
    for directory, _dirs, names in os.walk(path):
        for name in names:
            try:
                total += os.stat(os.path.join(directory, name)).st_size
            except OSError:
                # Disparu entre deux lectures : compté pour rien.
                logger.debug("Fichier illisible dans %s : %s", directory, name, exc_info=True)
                continue
    return total


# --- Constitution d'une action ------------------------------------------------


def open_action(session: Session, settings: Settings | None, media: Media, action: str) -> TrashAction | None:
    """Ouvre une action de corbeille pour la suppression en cours. `None` quand
    la corbeille est désactivée : les appelants suppriment alors normalement."""
    if not is_enabled(settings):
        return None
    row = TrashAction(action=action, media_title=media.title, media_type=media.media_type.value)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def close_action(session: Session, action: TrashAction | None) -> None:
    """Supprime l'action si elle n'a finalement rien recueilli (tout supprimé
    par Sonarr/Radarr, ou échec avant le premier élément) : une ligne vide dans
    la corbeille n'aiderait personne."""
    if action is None:
        return
    if session.exec(select(TrashItem.id).where(TrashItem.action_id == action.id)).first() is not None:
        return
    if action.arr_payload:
        return
    session.delete(action)
    session.commit()


# Ni `delete_or_trash` ni `trash_torrent` ne committent : l'appelant le fait en
# fin de suppression. Un commit au milieu d'une boucle vide les objets déjà
# marqués supprimés et casse la lecture de leurs attributs (bug réel).
def _remove_or_move(
    session: Session,
    settings: Settings | None,
    path: str,
    *,
    action: TrashAction | None,
    label: str,
) -> TrashItem | None:
    """Un seul fichier : supprimé, ou déplacé en corbeille quand une action est
    ouverte. Un fichier déjà absent n'est jamais une erreur — c'est le résultat
    voulu (Sonarr/Radarr peut l'avoir supprimé juste avant)."""
    if action is None or not is_enabled(settings):
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
        return None
    size = _size_of(path)
    try:
        trashed_path = move_to_trash(settings, path)
    except FileNotFoundError:
        # Déjà absent : rien à mettre de côté.
        logger.debug("Fichier déjà absent, rien à mettre de côté : %s", path)
        return None
    item = TrashItem(
        action_id=action.id,
        kind="library_file",
        label=label or os.path.basename(path),
        size=size,
        original_path=path,
        trashed_path=trashed_path,
    )
    session.add(item)
    return item


def delete_or_trash(
    session: Session,
    settings: Settings | None,
    path: str,
    *,
    action: TrashAction | None = None,
    label: str = "",
) -> TrashItem | None:
    """Supprime le fichier ET ses annexes (NFO, sous-titres, images : voir
    services/companion_files.py), ou les déplace en corbeille quand une action
    est ouverte. Renvoie l'élément du fichier vidéo, pour pouvoir tout remettre
    en place si l'étape suivante échoue.

    Les annexes suivent toujours la vidéo : les laisser derrière encombrait la
    bibliothèque, et ne pas les mettre de côté les perdait à la restauration
    (bug réel : des dizaines de NFO disparus)."""
    item = _remove_or_move(session, settings, path, action=action, label=label)
    for companion in companions(path):
        _remove_or_move(session, settings, companion, action=action, label=os.path.basename(companion))
    return item


def clean_media_folders(
    session: Session,
    settings: Settings | None,
    paths: list[str],
    *,
    action: TrashAction | None = None,
) -> None:
    """Après la suppression : un dossier qui n'a plus aucune vidéo n'a plus
    d'objet. Ce qu'il lui reste (NFO du dossier, affiche, fanart) part de la
    même façon que les fichiers, puis les dossiers vides sont retirés — jamais
    au-dessus de la racine de la bibliothèque."""
    root = (settings.emby_library_path or "").strip() if settings else ""
    if not root:
        return
    for folder in folders_of(paths):
        if not is_inside(folder, root) or has_video(folder):
            continue
        for leftover in leftovers(folder):
            _remove_or_move(session, settings, leftover, action=action, label=os.path.basename(leftover))
        prune_empty_dirs(folder, root)


def undo_items(session: Session, items: list[TrashItem]) -> None:
    """Remet ces éléments à leur place et les retire de la corbeille. Sert
    quand une étape ultérieure échoue : la promesse « si Sonarr/Radarr refuse,
    rien n'est supprimé du disque » vaut aussi avec la corbeille active."""
    for item in items:
        if item.trashed_path and item.original_path:
            try:
                _restore_path(item.trashed_path, item.original_path)
            except (OSError, FileExistsError):
                # Le fichier reste dans la corbeille, restaurable plus tard.
                logger.warning("Impossible de remettre %s en place", item.original_path, exc_info=True)
                continue
        if item.id is None:
            session.expunge(item)  # jamais enregistré : il suffit de l'oublier
        else:
            session.delete(item)


async def trash_torrent(
    session: Session,
    settings: Settings | None,
    client: TorrentClient,
    action: TrashAction,
    torrent: Torrent,
    paths: list[str],
) -> None:
    """Retire le torrent du client SANS supprimer ses données, puis déplace ces
    données en corbeille. Capture d'abord de quoi le ré-ajouter : fichier
    .torrent si le client sait l'exporter, sinon magnet reconstruit depuis ses
    trackers."""
    exported = await client.export_torrent(torrent.hash)
    trackers = [tracker.get("url", "") for tracker in await client.get_trackers(torrent.hash)]
    payload: dict[str, Any] = {
        "hash": torrent.hash,
        "name": torrent.name,
        "save_path": torrent.save_path,
        "category": torrent.category,
        "magnet": magnet_for(torrent.hash, torrent.name, [url for url in trackers if url]),
        "torrent_b64": base64.b64encode(exported).decode("ascii") if exported else None,
    }

    await client.delete_torrents([torrent.hash], delete_files=False)

    moved: list[str] = []
    originals: list[str] = []
    size = 0
    try:
        for path in paths:
            if not os.path.lexists(path):
                continue
            size += _size_of(path)
            moved.append(move_to_trash(settings, path))
            originals.append(path)
    finally:
        # Même si un déplacement échoue en cours de route, ce qui a bougé est
        # enregistré : sans cette ligne, des données déjà déplacées seraient
        # introuvables et donc irrécupérables.
        payload["paths"] = originals
        payload["trashed_paths"] = moved
        session.add(
            TrashItem(
                action_id=action.id,
                kind="torrent",
                label=torrent.name,
                size=size or (torrent.size or 0),
                original_path=originals[0] if originals else None,
                trashed_path=moved[0] if moved else None,
                torrent_payload=json.dumps(payload),
            )
        )


def capture_arr(
    session: Session, action: TrashAction | None, service: str, instance_id: int | None, body: dict | None
) -> None:
    """Mémorise la fiche Sonarr/Radarr AVANT son retrait : sans elle, un
    ré-ajout perdrait profil de qualité, dossier racine, tags et monitoring."""
    if action is None or not body:
        return
    action.arr_payload = json.dumps({"service": service, "instance_id": instance_id, "body": body})
    session.add(action)
    session.commit()


# --- Lecture, purge -----------------------------------------------------------


def actions(session: Session) -> list[TrashAction]:
    return list(session.exec(select(TrashAction).order_by(col(TrashAction.id).desc())).all())


def items_of(session: Session, action: TrashAction) -> list[TrashItem]:
    return list(
        session.exec(select(TrashItem).where(col(TrashItem.action_id) == action.id).order_by(col(TrashItem.id))).all()
    )


def payload_of(item: TrashItem) -> dict[str, Any]:
    try:
        payload = json.loads(item.torrent_payload or "{}")
    except ValueError:
        logger.warning("Données illisibles pour l'élément de corbeille %s", item.id)
        return {}
    return payload if isinstance(payload, dict) else {}


def trashed_paths(item: TrashItem) -> list[str]:
    if item.kind == "torrent":
        return [path for path in payload_of(item).get("trashed_paths") or [] if path]
    return [item.trashed_path] if item.trashed_path else []


def item_available(item: TrashItem) -> bool:
    """Faux dès qu'un élément a disparu de la corbeille : la restauration ne
    rendrait alors qu'une partie de la suppression."""
    paths = trashed_paths(item)
    return bool(paths) and all(os.path.lexists(path) for path in paths)


def _disk_files(path: str) -> list[str]:
    """Fichiers réguliers derrière un élément de corbeille : le fichier
    lui-même, ou tous ceux d'un dossier (données d'un torrent multi-fichiers).
    Les liens symboliques sont ignorés — les supprimer ne libère rien."""
    if os.path.islink(path):
        return []
    if os.path.isdir(path):
        found: list[str] = []
        for directory, _dirs, names in os.walk(path):
            for name in names:
                candidate = os.path.join(directory, name)
                if not os.path.islink(candidate):
                    found.append(candidate)
        return found
    return [path] if os.path.exists(path) else []


def reclaimable_sizes(session: Session, trash_actions: list[TrashAction]) -> dict[int, int]:
    """Espace que la purge libérerait RÉELLEMENT, action par action — jamais la
    somme des tailles (bug réel : une série et ses torrents hardlinkés étaient
    annoncés deux fois, et un fichier dont un lien vit encore hors de la
    corbeille comptait alors qu'il ne libère rien).

    Même règle que l'empreinte disque du dialogue de suppression
    (services/media_delete.py::reclaimed_bytes, lib/footprint.ts) : une unité
    disque ne compte que si la corbeille détient TOUS ses liens (`st_nlink`).
    Un lien subsistant ailleurs (fichier de bibliothèque conservé, torrent
    toujours en place) rend la purge sans effet sur l'espace libre. Le compte
    des liens est global : deux suppressions différentes peuvent détenir deux
    liens du même fichier, et l'unité est alors attribuée à une seule d'entre
    elles."""
    # (device, inode) -> [taille, nombre de liens réels, liens détenus par action]
    units: dict[tuple[int, int], tuple[int, int, dict[int, int]]] = {}
    for action in trash_actions:
        for item in items_of(session, action):
            for path in trashed_paths(item):
                for file_path in _disk_files(path):
                    try:
                        stat = os.stat(file_path)
                    except OSError:
                        # Illisible : compté pour rien, la purge ne libérera rien de sûr.
                        logger.debug("Fichier illisible dans la corbeille : %s", file_path, exc_info=True)
                        continue
                    key = (stat.st_dev, stat.st_ino)
                    size, links, holders = units.get(key, (stat.st_size, stat.st_nlink, {}))
                    holders[row_id(action)] = holders.get(row_id(action), 0) + 1
                    units[key] = (size, links, holders)

    sizes = {row_id(action): 0 for action in trash_actions}
    for size, links, holders in units.values():
        if sum(holders.values()) < links:
            continue  # un lien vit encore hors de la corbeille : rien à libérer
        owner = max(holders, key=lambda action_id: holders[action_id])
        sizes[owner] = sizes.get(owner, 0) + size
    return sizes


def purge_action(session: Session, action: TrashAction) -> None:
    """Suppression définitive : les éléments mis de côté sont effacés."""
    for item in items_of(session, action):
        for path in trashed_paths(item):
            _remove(path)
        session.delete(item)
    session.delete(action)
    session.commit()


def purge_expired(session: Session, settings: Settings | None) -> int:
    limit = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days(settings))
    expired = session.exec(select(TrashAction).where(TrashAction.created_at < limit)).all()
    for action in expired:
        purge_action(session, action)
    return len(expired)


# --- Restauration -------------------------------------------------------------


async def restore_action(
    session: Session, settings: Settings | None, action: TrashAction
) -> tuple[list[DeleteStepResult], bool]:
    """Remet tout en place : fichiers, torrents, suivi Sonarr/Radarr, demande
    Seer. Renvoie le détail par étape et si TOUT a réussi — une action dont une
    étape échoue reste dans la corbeille, avec ce qui n'a pas pu être rendu,
    pour réessayer sans rien avoir perdu."""
    steps: list[DeleteStepResult] = []
    items = items_of(session, action)

    restored_files = [item for item in items if item.kind == "library_file" and _restore_file(item, steps)]
    torrents = [item for item in items if item.kind == "torrent"]
    restored_torrents = await _restore_torrents(settings, torrents, steps)

    arr_ok = await _restore_arr(session, action, settings, steps)

    for item in restored_files + restored_torrents:
        session.delete(item)
    session.commit()

    complete = all(step.success for step in steps) and arr_ok
    if complete:
        purge_action(session, action)
    return steps, complete


def _restore_file(item: TrashItem, steps: list[DeleteStepResult]) -> bool:
    if not (item.trashed_path and item.original_path):
        return False
    try:
        _restore_path(item.trashed_path, item.original_path)
    except (OSError, FileExistsError) as exc:
        steps.append(DeleteStepResult(kind="library_file", label=item.label, success=False, error=str(exc)))
        return False
    steps.append(DeleteStepResult(kind="library_file", label=item.label, success=True))
    return True


async def _restore_torrents(
    settings: Settings | None, items: list[TrashItem], steps: list[DeleteStepResult]
) -> list[TrashItem]:
    """Données remises à leur place, puis torrent ré-ajouté au client : il
    vérifie les fichiers présents et reprend le seed sans rien retélécharger."""
    if not items:
        return []
    if not torrent_client_configured(settings):
        for item in items:
            steps.append(
                DeleteStepResult(kind="torrent", label=item.label, success=False, error="Client torrent non configuré.")
            )
        return []

    restored: list[TrashItem] = []
    try:
        async with torrent_client(settings) as client:
            for item in items:
                payload = payload_of(item)
                try:
                    # Relu en base : une liste plus courte que l'autre ne doit pas
                    # empêcher de remettre en place ce qui a une paire.
                    pairs = zip(payload.get("paths") or [], payload.get("trashed_paths") or [], strict=False)
                    for original, trashed in pairs:
                        _restore_path(trashed, original)
                    exported = payload.get("torrent_b64")
                    await client.add_torrent(
                        torrent=base64.b64decode(exported) if exported else None,
                        magnet=payload.get("magnet"),
                        save_path=payload.get("save_path"),
                        category=payload.get("category"),
                    )
                except (OSError, FileExistsError, httpx.HTTPError, ValueError, RuntimeError) as exc:
                    steps.append(DeleteStepResult(kind="torrent", label=item.label, success=False, error=str(exc)))
                    continue
                steps.append(DeleteStepResult(kind="torrent", label=item.label, success=True))
                restored.append(item)
    except (TorrentAuthError, httpx.HTTPError) as exc:
        steps.append(DeleteStepResult(kind="torrent", label=items[0].label, success=False, error=str(exc)))
    return restored


async def _restore_arr(
    session: Session, action: TrashAction, settings: Settings | None, steps: list[DeleteStepResult]
) -> bool:
    if not action.arr_payload:
        return True
    try:
        payload = json.loads(action.arr_payload)
    except ValueError:
        # Rien de lisible à recréer : la restauration des fichiers suit son cours.
        logger.warning("Fiche Sonarr/Radarr illisible pour l'action de corbeille %s", action.id)
        return True
    service = "radarr" if payload.get("service") == "radarr" else "sonarr"
    target = arr_target_by_id(session, settings, service, payload.get("instance_id"))
    if target is None:
        steps.append(
            DeleteStepResult(kind="arr_media", label=action.media_title, success=False, error="Instance introuvable.")
        )
        return False

    body = payload.get("body") or {}
    try:
        # Aucun rescan demandé ici : Sonarr/Radarr rafraîchit déjà la fiche
        # qu'il vient d'ajouter (MovieAddedHandler pousse RefreshMovie), et un
        # second scan lancé en parallèle enregistrait le même fichier et les
        # mêmes NFO plusieurs fois (bug réel : 2 fichiers et 3 NFO identiques
        # sur une fiche Radarr restaurée).
        if service == "radarr":
            await target.radarr().add_movie(body)
        else:
            await target.sonarr().add_series(body)
    except (httpx.HTTPError, ValueError) as exc:
        steps.append(DeleteStepResult(kind="arr_media", label=action.media_title, success=False, error=str(exc)))
        return False
    action.arr_payload = None
    session.add(action)
    session.commit()
    steps.append(DeleteStepResult(kind="arr_media", label=action.media_title, success=True))
    return True
