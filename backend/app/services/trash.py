"""Corbeille : une suppression de fichier devient un déplacement, annulable
pendant quelques jours.

Portée volontairement limitée aux fichiers qu'Analysarr supprime LUI-MÊME
(doublons jamais suivis, fichiers restants après un retrait Sonarr/Radarr,
nettoyage cascade). Quand Sonarr ou Radarr supprime le fichier à notre
demande, c'est LEUR corbeille qui s'applique, pas celle-ci — et les torrents
sont supprimés par le client torrent, hors de portée.

Le fichier est déplacé par `os.replace` : même inode, donc un hardlink encore
présent ailleurs reste valide, et aucune copie n'est faite (indispensable pour
des fichiers de plusieurs dizaines de Go). Le déplacement doit donc rester sur
le même système de fichiers : la corbeille de la racine de la bibliothèque
d'abord, sinon un dossier `.analysarr-trash` à côté du fichier."""

import os
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.models.settings import Settings
from app.models.trash import TrashEntry

TRASH_DIR_NAME = ".analysarr-trash"
DEFAULT_RETENTION_DAYS = 7
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 90


def is_enabled(settings: Settings | None) -> bool:
    return bool(settings and settings.trash_enabled)


def retention_days(settings: Settings | None) -> int:
    value = settings.trash_retention_days if settings else DEFAULT_RETENTION_DAYS
    return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, value))


def _unique_destination(directory: str, name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    candidate = os.path.join(directory, f"{stamp}-{name}")
    index = 1
    while os.path.lexists(candidate):
        candidate = os.path.join(directory, f"{stamp}-{index}-{name}")
        index += 1
    return candidate


def _move(path: str, trash_dir: str) -> str:
    os.makedirs(trash_dir, exist_ok=True)
    destination = _unique_destination(trash_dir, os.path.basename(path))
    os.replace(path, destination)
    return destination


def move_to_trash(settings: Settings | None, path: str) -> str:
    """Déplace le fichier et renvoie son chemin dans la corbeille. Lève l'erreur
    d'origine si le déplacement échoue : mieux vaut une suppression refusée
    qu'un fichier supprimé alors que l'utilisateur comptait sur la corbeille."""
    root = (settings.emby_library_path or "").strip() if settings else ""
    if root:
        try:
            return _move(path, os.path.join(root, TRASH_DIR_NAME))
        except OSError as exc:
            if exc.errno != 18:  # EXDEV : bibliothèque répartie sur plusieurs volumes
                raise
    return _move(path, os.path.join(os.path.dirname(path), TRASH_DIR_NAME))


def delete_or_trash(session: Session, settings: Settings | None, path: str, *, media_title: str, action: str) -> None:
    """Supprime le fichier, ou le déplace en corbeille si elle est activée."""
    if not is_enabled(settings):
        os.remove(path)
        return
    size = 0
    try:
        size = os.stat(path).st_size
    except OSError:
        pass
    trashed_path = move_to_trash(settings, path)
    session.add(
        TrashEntry(
            original_path=path,
            trashed_path=trashed_path,
            size=size,
            media_title=media_title,
            action=action,
        )
    )


def restore(session: Session, entry: TrashEntry) -> None:
    """Remet le fichier à sa place. Refuse si un fichier occupe déjà le chemin
    d'origine : on ne remplace jamais un fichier existant."""
    if os.path.lexists(entry.original_path):
        raise FileExistsError(f"Un fichier occupe déjà {entry.original_path}.")
    os.makedirs(os.path.dirname(entry.original_path), exist_ok=True)
    os.replace(entry.trashed_path, entry.original_path)
    session.delete(entry)
    session.commit()


def purge_entry(session: Session, entry: TrashEntry) -> None:
    try:
        os.remove(entry.trashed_path)
    except FileNotFoundError:
        pass  # déjà supprimé à la main : la ligne n'a plus de raison d'être
    session.delete(entry)
    session.commit()


def purge_expired(session: Session, settings: Settings | None) -> int:
    """Vide les entrées dont la rétention est écoulée. Renvoie le nombre
    d'entrées supprimées."""
    limit = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=retention_days(settings))
    expired = session.exec(select(TrashEntry).where(TrashEntry.deleted_at < limit)).all()
    for entry in expired:
        purge_entry(session, entry)
    return len(expired)


def entries(session: Session) -> list[TrashEntry]:
    return list(session.exec(select(TrashEntry).order_by(TrashEntry.id.desc())).all())
