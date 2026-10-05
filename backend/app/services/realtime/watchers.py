"""Guetteurs du temps réel : client torrent et serveur multimédia, interrogés
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
from app.services.realtime.hub import RealtimeHub
from app.services.realtime.status import StatusBoard
from app.services.realtime.targets import media_for_hashes, media_for_items
from app.services.watch_stats import excluded_user_ids

logger = logging.getLogger(__name__)

MASS_THRESHOLD = 50
MAX_BACKOFF = 300.0
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
        while True:
            try:
                await self.session()
            except SOURCE_ERRORS as exc:
                logger.warning("Temps réel %s : %s", self.key, describe(exc))
                self.board.failed(self.key, describe(exc))
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

    async def session(self) -> None:
        emby = media_server_client(self.settings)
        if emby is None:
            raise RuntimeError("Serveur multimédia non configuré.")
        since = datetime.now(UTC) - OVERLAP
        tick = 0
        while True:
            started = datetime.now(UTC)
            await self.poll_library(emby, since)
            if tick % WATCH_EVERY == 0:
                await self.poll_watch(emby, since)
            self.board.checked(self.key)
            self.seen.prune(time.monotonic())
            since = started - OVERLAP
            tick += 1
            await asyncio.sleep(self.interval)

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
