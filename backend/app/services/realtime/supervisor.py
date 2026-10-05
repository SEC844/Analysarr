"""Démarre, arrête et reconfigure le temps réel selon les réglages.

Appliqué au démarrage (`main.lifespan`), aussitôt après une modification
des réglages du temps réel, et vérifié toutes les `CONFIG_CHECK_SECONDS` :
quel que soit l'écran qui change l'adresse du client torrent, une clé API ou
un intervalle, le guetteur concerné suit sans câblage supplémentaire. Un
guetteur n'est redémarré que si SA configuration a changé : enregistrer un
autre réglage ne coupe rien.

Inactif tant que l'application n'a pas démarré (`start`) : les tests, qui
n'exécutent pas le cycle de vie, ne lancent jamais de tâche de fond."""

import asyncio
import contextlib
import hashlib
import logging
from dataclasses import dataclass

from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from app.clients.emby import media_server_client
from app.clients.torrent import torrent_client_configured
from app.database import engine
from app.models.arr_webhook import ArrWebhook
from app.models.settings import Settings
from app.services.events import live_events
from app.services.realtime.hub import RealtimeHub, hub
from app.services.realtime.status import StatusBoard, board
from app.services.realtime.watchers import MediaServerWatcher, TorrentWatcher, Watcher
from app.services.realtime.webhooks import source_key

logger = logging.getLogger(__name__)

CONFIG_CHECK_SECONDS = 5.0


@dataclass(frozen=True)
class _Snapshot:
    settings: Settings | None
    webhooks: list[tuple[str, int]]


def _load() -> _Snapshot:
    with Session(engine) as session:
        settings = session.get(Settings, 1)
        if settings is not None:
            session.expunge(settings)
        rows = session.exec(select(ArrWebhook)).all()
        return _Snapshot(settings, [(row.service, row.instance_id) for row in rows if row.notification_id is not None])


def _signature(*parts: object) -> str:
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def _desired(settings: Settings) -> dict[str, tuple[type[Watcher], float, str]]:
    """Guetteurs voulus : (classe, intervalle, empreinte de leur configuration)."""
    desired: dict[str, tuple[type[Watcher], float, str]] = {}
    if settings.realtime_torrents_enabled and torrent_client_configured(settings):
        interval = float(settings.realtime_torrents_interval)
        config = _signature(
            settings.torrent_client,
            settings.qbittorrent_url,
            settings.qbittorrent_username,
            settings.qbittorrent_password,
            interval,
        )
        desired[TorrentWatcher.key] = (TorrentWatcher, interval, config)
    if settings.realtime_media_server_enabled and media_server_client(settings) is not None:
        interval = float(settings.realtime_media_server_interval)
        config = _signature(
            settings.media_server,
            settings.emby_url,
            settings.emby_api_key,
            settings.excluded_emby_user_ids,
            interval,
        )
        desired[MediaServerWatcher.key] = (MediaServerWatcher, interval, config)
    return desired


class RealtimeSupervisor:
    def __init__(self, hub: RealtimeHub, board: StatusBoard) -> None:
        self.hub = hub
        self.board = board
        self.running = False
        self._tasks: dict[str, tuple[asyncio.Task[None], str]] = {}
        self._lock = asyncio.Lock()
        self._config_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.running = True
        self.hub.start()
        await self.apply()
        self._config_task = asyncio.create_task(self._follow_config(), name="realtime-config")

    async def stop(self) -> None:
        self.running = False
        if self._config_task is not None:
            self._config_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._config_task
            self._config_task = None
        for key in list(self._tasks):
            await self._stop(key)
        await self.hub.stop()
        self.board.clear()

    async def apply(self, *, announce: bool = True) -> None:
        if not self.running:
            return
        async with self._lock:
            snapshot = await run_in_threadpool(_load)
            settings = snapshot.settings
            desired = _desired(settings) if settings is not None else {}
            self.hub.debounce = float(settings.realtime_debounce_seconds) if settings is not None else self.hub.debounce
            for key in [k for k in self._tasks if k not in desired or self._tasks[k][1] != desired[k][2]]:
                await self._stop(key)
            for key, (watcher_class, interval, config) in desired.items():
                if key not in self._tasks and settings is not None:
                    watcher = watcher_class(settings, interval, self.hub, self.board)
                    task = asyncio.create_task(watcher.run(), name=f"realtime-{key}")
                    self._tasks[key] = (task, config)
            webhook_keys = {source_key(service, instance_id) for service, instance_id in snapshot.webhooks}
            for status in self.board.snapshot():
                if status.kind == "webhook" and status.key not in webhook_keys:
                    self.board.forget(status.key)
            for key in webhook_keys:
                self.board.register(key, "webhook")
        if announce:
            await live_events.publish({"type": "realtime.status"})

    async def _follow_config(self) -> None:
        while True:
            await asyncio.sleep(CONFIG_CHECK_SECONDS)
            try:
                await self.apply(announce=False)
            except Exception:  # noqa: BLE001 - la surveillance ne doit jamais s'arrêter
                logger.exception("Configuration du temps réel illisible")

    async def _stop(self, key: str) -> None:
        task, _ = self._tasks.pop(key)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self.board.forget(key)

    def active_keys(self) -> list[str]:
        return sorted(self._tasks)


supervisor = RealtimeSupervisor(hub, board)
