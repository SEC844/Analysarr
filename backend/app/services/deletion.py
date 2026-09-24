"""Suppression en tout ou rien.

Une suppression touche trois systèmes qui peuvent échouer séparément : le
disque, le client torrent et Sonarr/Radarr. Elle se déroule donc comme une
transaction :

1. tout est vérifié avant de commencer (montages, droits d'écriture, fiche
   Sonarr/Radarr lisible) : un refus à ce stade ne touche à rien ;
2. ce qui part est d'abord MIS DE CÔTÉ dans une action de corbeille — même
   corbeille désactivée — et Sonarr/Radarr ne supprime jamais de fichier lui-
   même (`deleteFiles=false`) ;
3. au moindre échec, tout est remis en place : fichiers, torrents ré-ajoutés
   au client, fiche Sonarr/Radarr recréée ou relue, épisodes resurveillés ;
4. tout a réussi : corbeille active, l'action y reste ; sinon elle est purgée.

Bug réel à l'origine de ce module : une suppression retirait le torrent du
client, puis échouait sur le fichier de la bibliothèque (droits) — le média
n'était plus seedé, restait dans la bibliothèque, et la corbeille ne pouvait
rien rendre."""

import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx
from sqlmodel import Session, col, select

from app.clients.torrent import TorrentAuthError, torrent_client
from app.models.media import Media, Torrent, TorrentFile
from app.models.settings import Settings
from app.models.trash import TrashAction
from app.schemas.media import DeleteStepResult
from app.services.path_guard import MountUnavailableError, ensure_writable
from app.services.trash import (
    capture_arr,
    clean_media_folders,
    close_action,
    is_enabled,
    move_aside,
    open_action,
    purge_action,
    restore_action,
    staging_blockers,
    trash_torrent,
)

logger = logging.getLogger(__name__)

Undo = Callable[[], Awaitable[object]]


class DeletionFailed(Exception):
    """Une étape a échoué : la transaction doit être annulée."""

    def __init__(self, step: DeleteStepResult) -> None:
        super().__init__(step.error or step.label)
        self.step = step


def torrent_paths(session: Session, torrent: Torrent) -> list[str]:
    """Éléments à déplacer pour mettre les données du torrent de côté :
    `content_path` (le fichier lui-même, ou le dossier racine d'un torrent
    multi-fichiers), sinon les chemins mémorisés au dernier scan."""
    if torrent.content_path:
        return [torrent.content_path]
    rows = session.exec(select(TorrentFile.path).where(col(TorrentFile.torrent_hash) == torrent.hash)).all()
    return [path for path in rows if path]


def _visible(paths: Sequence[str]) -> bool:
    return any(os.path.lexists(path) for path in paths)


def ensure_deletable(
    session: Session,
    settings: Settings,
    file_paths: Sequence[str],
    torrents: Sequence[Torrent],
    action: str,
) -> None:
    """Refuse l'action d'emblée si un élément ne pourrait pas être mis de côté.

    Données d'un torrent introuvables depuis le conteneur : impossibles à
    garder en corbeille, la suppression est refusée ; corbeille désactivée, le
    client les supprimera lui-même en dernière étape."""
    blocked = [path for file_path in file_paths for path in staging_blockers(settings, file_path)]
    unreachable = []
    for torrent in torrents:
        paths = torrent_paths(session, torrent)
        if not _visible(paths):
            unreachable.append(paths[0] if paths else torrent.name)
            continue
        blocked += [path for data_path in paths for path in staging_blockers(settings, data_path)]
    if unreachable and is_enabled(settings):
        raise MountUnavailableError(
            f"{action} annulée : les données de ces torrents sont introuvables depuis le conteneur Analysarr, "
            f"elles ne pourraient pas être gardées en corbeille. Montez le dossier de téléchargement. "
            f"Aucune modification n'a été effectuée. Chemins : {', '.join(unreachable[:3])}"
        )
    ensure_writable(blocked, action)


class DeletionTransaction:
    """Une suppression, mise de côté élément par élément puis validée ou
    annulée d'un bloc (voir la docstring du module)."""

    def __init__(self, session: Session, settings: Settings, media: Media, action_name: str) -> None:
        self.session = session
        self.settings = settings
        self.action: TrashAction = open_action(session, media, action_name)
        self._staged_paths: list[str] = []
        self._client_deletions: list[Torrent] = []
        self._undo: list[Undo] = []
        self._arr_media_removed = False

    # --- Étapes ------------------------------------------------------------------

    def stage_file(self, path: str, label: str) -> None:
        try:
            move_aside(self.session, self.settings, path, action=self.action, label=label)
        except OSError as exc:
            raise DeletionFailed(
                DeleteStepResult(kind="library_file", label=label, success=False, error=str(exc))
            ) from exc
        self._staged_paths.append(path)

    async def capture_arr(
        self, service: str, instance_id: int | None, read: Callable[[], Awaitable[dict[str, Any] | None]]
    ) -> None:
        """Fiche Sonarr/Radarr lue AVANT son retrait : c'est elle qui permet de
        la recréer, à l'annulation comme à la restauration depuis la corbeille.
        Illisible : Sonarr/Radarr est injoignable, le retrait échouerait aussi."""
        try:
            body = await read()
        except (httpx.HTTPError, ValueError) as exc:
            raise DeletionFailed(
                DeleteStepResult(kind="arr_media", label=service, success=False, error=str(exc))
            ) from exc
        capture_arr(self.session, self.action, service, instance_id, body)

    def arr_media_removed(self) -> None:
        """Le média a quitté Sonarr/Radarr : l'annulation le recrée."""
        self._arr_media_removed = True

    def on_rollback(self, undo: Undo) -> None:
        """Geste à rejouer en cas d'annulation (relire un film, resurveiller
        des épisodes), après la remise en place des fichiers."""
        self._undo.append(undo)

    async def stage_torrents(self, torrents: Sequence[Torrent]) -> None:
        """Torrents retirés du client SANS leurs données, qui sont mises de
        côté. Données introuvables depuis le conteneur (corbeille désactivée,
        voir `ensure_deletable`) : laissées au client, supprimées en dernier."""
        staged = []
        for torrent in torrents:
            if _visible(torrent_paths(self.session, torrent)):
                staged.append(torrent)
            else:
                self._client_deletions.append(torrent)
        if not staged:
            return
        current = staged[0]
        try:
            async with torrent_client(self.settings) as client:
                for current in staged:
                    await trash_torrent(
                        self.session, self.settings, client, self.action, current, torrent_paths(self.session, current)
                    )
        except (TorrentAuthError, httpx.HTTPError, OSError, RuntimeError) as exc:
            raise DeletionFailed(
                DeleteStepResult(kind="torrent", label=current.name, success=False, error=str(exc))
            ) from exc

    async def delete_unreachable_torrents(self) -> None:
        """Dernière étape, irréversible : torrents dont Analysarr ne voit pas
        les données, supprimés par le client lui-même."""
        if not self._client_deletions:
            return
        try:
            async with torrent_client(self.settings) as client:
                await client.delete_torrents([t.hash for t in self._client_deletions], delete_files=True)
        except (TorrentAuthError, httpx.HTTPError, RuntimeError) as exc:
            raise DeletionFailed(
                DeleteStepResult(
                    kind="torrent", label=self._client_deletions[0].name, success=False, error=str(exc)
                )
            ) from exc

    # --- Issue ---------------------------------------------------------------------

    def commit(self) -> None:
        """Tout a réussi : dossiers vidés rangés avec le reste, puis l'action
        reste en corbeille (active) ou est purgée (désactivée)."""
        try:
            clean_media_folders(self.session, self.settings, self._staged_paths, action=self.action)
        except OSError:
            # Confort seulement (NFO de dossier, dossiers vides) : la suppression
            # elle-même a réussi, les restes seront visibles dans la bibliothèque.
            logger.warning("Nettoyage des dossiers vidés incomplet", exc_info=True)
        self.session.commit()
        if is_enabled(self.settings):
            close_action(self.session, self.action)
            return
        try:
            purge_action(self.session, self.action)
        except OSError:
            # Les éléments restent dans la corbeille, d'où ils peuvent être purgés.
            logger.warning("Purge incomplète de la suppression %s", self.action.id, exc_info=True)

    async def rollback(self, failure: DeletionFailed) -> list[DeleteStepResult]:
        """Remet tout en place. Renvoie l'étape en échec, suivie du bilan de
        l'annulation — les étapes annulées ne sont pas présentées comme faites."""
        if not self._arr_media_removed:
            # Le média n'a pas quitté Sonarr/Radarr : rien à recréer.
            self.action.arr_payload = None
            self.session.add(self.action)
        restore_steps, complete = await restore_action(self.session, self.settings, self.action)
        errors = [f"{step.label} : {step.error}" for step in restore_steps if not step.success]
        for undo in reversed(self._undo):
            try:
                await undo()
            except httpx.HTTPError as exc:
                complete = False
                errors.append(str(exc))
        # Étape en échec dans les deux cas : l'action n'a pas eu lieu, et
        # l'historique comme les notifications doivent le dire.
        if complete:
            summary = DeleteStepResult(
                kind="rollback", label="Suppression annulée : tout a été remis en place.", success=False
            )
        else:
            logger.warning("Annulation incomplète : %s", "; ".join(errors))
            summary = DeleteStepResult(
                kind="rollback_incomplete",
                label="Suppression annulée, mais certains éléments n'ont pas pu être remis en place : "
                "ils restent dans la corbeille, d'où ils peuvent être restaurés.",
                success=False,
                error="; ".join(errors),
            )
        return [failure.step, summary]
