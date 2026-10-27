"""Guetteurs du temps réel : client torrent, serveur multimédia,
gestionnaire de demandes et Sonarr/Radarr (lecture seule), interrogés
toutes les quelques secondes par leur voie la moins coûteuse.

Base commune (`Watcher`) : boucle d'interrogation, état publié dans le
tableau des sources (`status.board`), backoff exponentiel en cas d'erreur
(plafonné à 5 min, remis à zéro au premier succès). Un guetteur ne fait que
SIGNALER à la file (`hub`) : il ne lit ni n'écrit jamais le cache média
lui-même, en dehors de la correspondance identifiant → média.

Garde-fou commun : au-delà de `MASS_THRESHOLD` médias touchés d'un coup
(bibliothèque réanalysée par le serveur, client torrent redémarré), une seule
analyse du service remplace des centaines d'analyses individuelles."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from app.clients.emby import EmbyClient, media_server_client
from app.clients.torrent import torrent_client
from app.clients.torrent_base import Fingerprint, TorrentAuthError
from app.database import engine
from app.models.media import EmbyUser
from app.models.settings import Settings
from app.services.arr_instances import ArrTarget
from app.services.realtime.hub import RealtimeHub
from app.services.realtime.status import StatusBoard
from app.services.realtime.targets import media_for_arr_ids, media_for_hashes, media_for_items
from app.services.realtime.webhooks import recently_delivered
from app.services.seer import seer_client
from app.services.watch_stats import excluded_user_ids

logger = logging.getLogger(__name__)

MASS_THRESHOLD = 50
MAX_BACKOFF = 300.0
# Intervalles d'interrogation, sans réglage : assez courts pour voir un
# changement en quelques secondes, assez longs pour ne rien coûter.
TORRENT_INTERVAL = 3.0
MEDIA_SERVER_INTERVAL = 5.0
REQUESTS_INTERVAL = 15.0
# Erreurs d'une source, jamais fatales : la boucle réessaie plus tard.
SOURCE_ERRORS = (httpx.HTTPError, TorrentAuthError, RuntimeError, ValueError, KeyError, TypeError)


def describe(exc: BaseException) -> str:
    """Message d'erreur affichable : jamais d'URL (elle peut porter un jeton)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return f"{type(exc).__name__} : {exc}" if not isinstance(exc, httpx.HTTPError) else type(exc).__name__


class Watcher(ABC):
    key: str
    kind: str

    def __init__(self, settings: Settings, interval: float, hub: RealtimeHub, board: StatusBoard) -> None:
        self.settings = settings
        self.interval = interval
        self.hub = hub
        self.board = board

    async def run(self) -> None:
        self.board.register(self.key, self.kind)
        backoff = self.interval
        last_error: str | None = None
        while True:
            try:
                await self.session()
            except SOURCE_ERRORS as exc:
                message = describe(exc)
                # Une panne qui dure n'écrit qu'une ligne, pas une par essai ;
                # une nouvelle panne après un retour à la normale en réécrit une.
                status = self.board.get(self.key)
                if message != last_error or (status is not None and status.state == "active"):
                    logger.warning("Temps réel %s : %s", self.key, message)
                    last_error = message
                self.board.failed(self.key, message)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
            else:
                backoff = self.interval

    @abstractmethod
    async def session(self) -> None:
        """Ouvre la connexion et interroge en boucle ; une exception ferme la
        session, la base réessaie après le backoff."""


# --- Client torrent --------------------------------------------------------------


@dataclass(frozen=True)
class TorrentChanges:
    changed: set[str]
    removed: set[str]
    added: set[str]

    def __bool__(self) -> bool:
        return bool(self.changed or self.removed or self.added)


def diff_fingerprints(previous: dict[str, Fingerprint], current: dict[str, Fingerprint]) -> TorrentChanges:
    return TorrentChanges(
        changed={h for h, fp in current.items() if h in previous and previous[h] != fp},
        removed=set(previous) - set(current),
        added=set(current) - set(previous),
    )


class TorrentWatcher(Watcher):
    key = "torrents"
    kind = "torrents"

    async def session(self) -> None:
        async with torrent_client(self.settings) as client:
            previous: dict[str, Fingerprint] | None = None
            while True:
                current = await client.fingerprints()
                self.board.checked(self.key)
                if previous is not None:
                    changes = diff_fingerprints(previous, current)
                    if changes:
                        self.board.event(self.key)
                        await self.dispatch(changes, current)
                previous = current
                await asyncio.sleep(self.interval)

    async def dispatch(self, changes: TorrentChanges, current: dict[str, Fingerprint]) -> None:
        resolved = await run_in_threadpool(media_for_hashes, changes.changed | changes.removed | changes.added)
        if len(resolved.media_ids) > MASS_THRESHOLD:
            self.hub.service_changed("torrents")
            return
        for media_id in resolved.media_ids:
            self.hub.media_changed(media_id)
        # Un torrent qu'aucun média ne porte encore (ajout manuel, copie
        # cross-seed) ne se rattache que par une analyse du client torrent —
        # seulement une fois téléchargé : avant, il n'a rien à rattacher.
        if any(current[h][4] for h in resolved.unknown if h in current):
            self.hub.service_changed("torrents")


# --- Serveur multimédia -------------------------------------------------------------

# Recouvrement entre deux lectures « changé depuis » : décalage d'horloge
# entre conteneurs, latence. Les doublons qu'il produit sont écartés par la
# signature de chaque élément (`SeenItems`).
OVERLAP = timedelta(seconds=30)
PAGE_LIMIT = 500
# Visionnage : un appel par compte, interrogé deux fois moins souvent.
WATCH_EVERY = 2


class SeenItems:
    """Éléments déjà signalés, avec leur signature (`DateLastSaved`, `Etag`,
    ou état de lecture). Un élément revu dans la fenêtre de recouvrement avec
    la même signature n'est pas resignalé ; sans signature, il ne l'est pas
    avant la fin de la fenêtre."""

    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._seen: dict[str, tuple[Any, float]] = {}

    def is_new(self, key: str, signature: Any, now: float) -> bool:
        previous = self._seen.get(key)
        self._seen[key] = (signature, now)
        if previous is None:
            return True
        old_signature, seen_at = previous
        if signature is not None:
            return signature != old_signature
        return now - seen_at > self.ttl

    def prune(self, now: float) -> None:
        for key in [k for k, (_, seen_at) in self._seen.items() if now - seen_at > 2 * self.ttl]:
            del self._seen[key]


def media_item_id(item: dict[str, Any]) -> str | None:
    """Élément qui porte un média dans Analysarr : le film ou la série (un
    épisode ou une saison est ramené à sa série)."""
    if item.get("Type") in ("Episode", "Season"):
        series_id = item.get("SeriesId")
        return series_id if isinstance(series_id, str) and series_id else None
    item_id = item.get("Id")
    return item_id if isinstance(item_id, str) and item_id else None


def _user_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    data = item.get("UserData") or {}
    keys = ("Played", "PlaybackPositionTicks", "PlayCount", "IsFavorite", "LastPlayedDate")
    return tuple(data.get(key) for key in keys)


def _watched_users(settings: Settings) -> list[str]:
    excluded = excluded_user_ids(settings)
    with Session(engine) as session:
        users = session.exec(select(EmbyUser)).all()
    return [u.id for u in users if not u.is_disabled and u.id not in excluded]


class MediaServerWatcher(Watcher):
    key = "media_server"
    kind = "media_server"

    def __init__(self, settings: Settings, interval: float, hub: RealtimeHub, board: StatusBoard) -> None:
        super().__init__(settings, interval, hub, board)
        self.seen = SeenItems(OVERLAP.total_seconds())
        self.counts: tuple[int, int, int] | None = None
        self.counts_supported = True

    async def session(self) -> None:
        emby = media_server_client(self.settings)
        if emby is None:
            raise RuntimeError("Serveur multimédia non configuré.")
        since = datetime.now(UTC) - OVERLAP
        tick = 0
        self.counts = None
        while True:
            started = datetime.now(UTC)
            await self.poll_counts(emby)
            await self.poll_library(emby, since)
            if tick % WATCH_EVERY == 0:
                await self.poll_watch(emby, since)
            self.board.checked(self.key)
            self.seen.prune(time.monotonic())
            since = started - OVERLAP
            tick += 1
            await asyncio.sleep(self.interval)

    async def poll_counts(self, emby: EmbyClient) -> None:
        """Suppression : un élément RETIRÉ n'apparaît dans aucune lecture
        « changé depuis ». Les compteurs de la bibliothèque le trahissent ;
        une baisse demande une analyse du serveur multimédia (qui retire le
        fichier, ou le média s'il n'est plus nulle part). Serveur sans
        compteurs : on s'en passe, le scan de nuit rattrape."""
        if not self.counts_supported:
            return
        try:
            counts = await emby.get_item_counts()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (400, 404):
                raise
            logger.info("Compteurs de bibliothèque indisponibles : suppressions vues au scan de nuit")
            self.counts_supported = False
            return
        if self.counts is not None and any(now < before for now, before in zip(counts, self.counts, strict=True)):
            self.board.event(self.key)
            self.hub.service_changed("media_server")
        self.counts = counts

    async def poll_library(self, emby: EmbyClient, since: datetime) -> None:
        items = await emby.get_changed_items(since, PAGE_LIMIT)
        now = time.monotonic()
        changed = [
            item
            for item in items
            if self.seen.is_new(f"item:{item.get('Id')}", item.get("DateLastSaved") or item.get("Etag"), now)
        ]
        await self._signal(changed, full=len(items) >= PAGE_LIMIT, watch_only=False)

    async def poll_watch(self, emby: EmbyClient, since: datetime) -> None:
        users = await run_in_threadpool(_watched_users, self.settings)
        changed: list[dict[str, Any]] = []
        overflow = False
        for user_id in users:
            items = await emby.get_user_changed_items(user_id, since, PAGE_LIMIT)
            overflow = overflow or len(items) >= PAGE_LIMIT
            now = time.monotonic()
            changed += [i for i in items if self.seen.is_new(f"user:{user_id}:{i.get('Id')}", _user_signature(i), now)]
        await self._signal(changed, full=overflow, watch_only=True)

    async def _signal(self, items: Iterable[dict[str, Any]], *, full: bool, watch_only: bool) -> None:
        item_ids = {item_id for item in items if (item_id := media_item_id(item))}
        if not item_ids and not full:
            return
        self.board.event(self.key)
        resolved = await run_in_threadpool(media_for_items, item_ids)
        scope = "watch" if watch_only else "media_server"
        if full or len(resolved.media_ids) > MASS_THRESHOLD:
            self.hub.service_changed(scope)
            return
        for media_id in resolved.media_ids:
            self.hub.media_changed(media_id, watch_only=watch_only)
        if resolved.unknown and not watch_only:
            # Nouvel élément dans la bibliothèque : l'analyse du serveur
            # multimédia le rattache (ou en fait un média non suivi).
            self.hub.service_changed("media_server")


# --- Gestionnaire de demandes ------------------------------------------------------


class RequestsWatcher(Watcher):
    """Seer (Overseerr, Jellyseerr) ou Ombi : une empreinte légère des
    demandes (voir `requests_fingerprint` de chaque client) ; au moindre
    changement, une analyse des demandes. Lecture seule, comme toujours : rien
    n'est configuré chez eux (leur unique webhook appartient à l'utilisateur)."""

    key = "requests"
    kind = "requests"

    async def session(self) -> None:
        client = seer_client(self.settings)
        if client is None:
            raise RuntimeError("Gestionnaire de demandes non configuré.")
        previous: tuple[Any, ...] | None = None
        while True:
            current = await client.requests_fingerprint()
            self.board.checked(self.key)
            if previous is not None and current != previous:
                self.board.event(self.key)
                self.hub.service_changed("seer")
            previous = current
            await asyncio.sleep(self.interval)


# --- Sonarr / Radarr (lecture seule) -------------------------------------------------

ARR_INTERVAL = 10.0
HISTORY_PAGE = 20
# Événements d'historique qui font exister un média dans Analysarr (fichier
# importé). Pour un film ou une série encore inconnus, seuls ceux-là demandent
# une analyse du service : un simple grab n'a encore rien à afficher.
IMPORT_EVENTS = frozenset({"downloadFolderImported", "movieFolderImported", "seriesFolderImported"})
_QUEUE_COUNTERS = ("totalCount", "errors", "warnings", "unknownErrors", "unknownWarnings")


def arr_source_key(service: str, instance_id: int) -> str:
    return f"arr:{service}:{instance_id}"


def _record_id(record: dict[str, Any]) -> int | None:
    value = record.get("id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class ArrWatcher(Watcher):
    """Sonarr ou Radarr suivi SANS rien configurer chez lui : derniers
    événements de son historique (grab, import, suppression, renommage d'un
    fichier) et compteurs de sa file d'attente, toutes les `ARR_INTERVAL`.

    Toujours actif, webhook ou pas : sans webhook (désactivé, adresse
    d'Analysarr injoignable), c'est lui qui assure le temps réel ; avec, il
    sert de filet de sécurité — un média que le webhook vient de signaler
    n'est pas réanalysé une seconde fois (`webhooks.recently_delivered`).
    Ce que l'historique ne dit pas (fiche ajoutée ou retirée sans fichier) est
    vu par le webhook, sinon par le scan de nuit."""

    kind = "arr"

    def __init__(
        self, settings: Settings, interval: float, hub: RealtimeHub, board: StatusBoard, target: ArrTarget
    ) -> None:
        super().__init__(settings, interval, hub, board)
        self.target = target
        self.instance_id = target.instance_id or 0
        self.key = arr_source_key(target.kind, self.instance_id)

    async def session(self) -> None:
        client = self.target.sonarr() if self.target.kind == "sonarr" else self.target.radarr()
        last_id: int | None = None
        queue: tuple[Any, ...] | None = None
        while True:
            records = await client.get_recent_history(HISTORY_PAGE)
            status = await client.get_queue_status()
            current_queue = tuple(status.get(name) for name in _QUEUE_COUNTERS)
            self.board.checked(self.key)
            newest = max((rid for r in records if (rid := _record_id(r)) is not None), default=0)
            if last_id is not None:
                fresh = [r for r in records if (rid := _record_id(r)) is not None and rid > last_id]
                await self.dispatch(fresh, overflow=len(fresh) >= HISTORY_PAGE)
            if queue is not None and current_queue != queue:
                # Import bloqué, téléchargement en souffrance : la file est relue.
                self.board.event(self.key)
                self.hub.service_changed("queue")
            last_id = max(newest, last_id or 0)
            queue = current_queue
            await asyncio.sleep(self.interval)

    async def dispatch(self, records: list[dict[str, Any]], *, overflow: bool) -> None:
        if overflow:
            # Plus d'événements qu'une page : la liste du service est relue.
            self.board.event(self.key)
            self.hub.service_changed(self.target.kind)
            return
        field = "seriesId" if self.target.kind == "sonarr" else "movieId"
        imported: dict[int, bool] = {}
        for record in records:
            arr_id = record.get(field)
            if not isinstance(arr_id, int) or isinstance(arr_id, bool) or arr_id <= 0:
                continue
            if recently_delivered(self.target.kind, self.instance_id, arr_id):
                continue
            imported[arr_id] = imported.get(arr_id, False) or record.get("eventType") in IMPORT_EVENTS
        if not imported:
            return
        self.board.event(self.key)
        known = await run_in_threadpool(media_for_arr_ids, self.target.kind, self.target.instance_id, list(imported))
        for media_id in known.values():
            self.hub.media_changed(media_id)
        if any(was_imported for arr_id, was_imported in imported.items() if arr_id not in known):
            # Nouveau film ou nouvelle série importés : seule l'analyse du
            # service les ajoute.
            self.hub.service_changed(self.target.kind)
