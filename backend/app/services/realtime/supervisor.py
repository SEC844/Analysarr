"""Démarre, arrête et reconfigure le temps réel selon les services
configurés : il est la norme, sans réglage. Chaque service configuré est
suivi (client torrent, serveur multimédia, gestionnaire de demandes) et
chaque instance Sonarr/Radarr reçoit son webhook automatiquement.

Appliqué au démarrage (`main.lifespan`) et vérifié toutes les
`CONFIG_CHECK_SECONDS` : quel que soit l'écran qui change une adresse ou une
clé, le guetteur concerné suit sans câblage supplémentaire. Un guetteur n'est
redémarré que si SA configuration a changé : enregistrer un autre réglage ne
coupe rien. Un webhook refusé (Sonarr/Radarr injoignable, adresse
d'Analysarr qu'il ne joint pas) est retenté au plus toutes les
`WEBHOOK_RETRY_SECONDS`, ou aussitôt sur demande.

Inactif tant que l'application n'a pas démarré (`start`) : les tests, qui
n'exécutent pas le cycle de vie, ne lancent jamais de tâche de fond."""

import asyncio
import contextlib
import hashlib
import logging
import time
from dataclasses import dataclass

from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app.clients.emby import media_server_client
from app.clients.torrent import torrent_client_configured
from app.database import engine
from app.models.settings import Settings
from app.services.events import live_events
from app.services.realtime import webhooks
from app.services.realtime.hub import RealtimeHub, hub
from app.services.realtime.status import StatusBoard, board
from app.services.realtime.watchers import (
    MEDIA_SERVER_INTERVAL,
    REQUESTS_INTERVAL,
    TORRENT_INTERVAL,
    MediaServerWatcher,
    RequestsWatcher,
    TorrentWatcher,
    Watcher,
)
from app.services.seer import seer_configured

logger = logging.getLogger(__name__)

CONFIG_CHECK_SECONDS = 5.0
WEBHOOK_RETRY_SECONDS = 900.0
NO_ADDRESS = "Adresse d'Analysarr inconnue : Réglages → Application."


@dataclass(frozen=True)
class _Snapshot:
    settings: Settings | None
    base_url: str
    # Webhooks voulus, et lesquels sont déjà branchés et à jour.
    wanted: list[webhooks.WantedWebhook]
    connected: set[str]


def _load() -> _Snapshot:
    with Session(engine) as session:
        settings = session.get(Settings, 1)
        wanted = webhooks.wanted_webhooks(session, settings)
        webhooks.forget_orphans(session, wanted)
        base_url = settings.analysarr_url if settings is not None else ""
        connected = set()
        for w in wanted:
            row = webhooks.find_row(session, w.target.kind, w.instance_id)
            if webhooks.is_connected(row, w.target, base_url, w.instance_id):
                connected.add(w.key)
        if settings is not None:
            session.expunge(settings)
        return _Snapshot(settings, base_url, wanted, connected)


def _signature(*parts: object) -> str:
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def _desired(settings: Settings) -> dict[str, tuple[type[Watcher], float, str]]:
    """Guetteurs voulus : (classe, intervalle, empreinte de leur configuration)."""
    desired: dict[str, tuple[type[Watcher], float, str]] = {}
    if torrent_client_configured(settings):
        config = _signature(
            settings.torrent_client,
            settings.qbittorrent_url,
            settings.qbittorrent_username,
            settings.qbittorrent_password,
            TORRENT_INTERVAL,
        )
        desired[TorrentWatcher.key] = (TorrentWatcher, TORRENT_INTERVAL, config)
    if media_server_client(settings) is not None:
        config = _signature(
            settings.media_server,
            settings.emby_url,
            settings.emby_api_key,
            settings.excluded_emby_user_ids,
            MEDIA_SERVER_INTERVAL,
        )
        desired[MediaServerWatcher.key] = (MediaServerWatcher, MEDIA_SERVER_INTERVAL, config)
    if seer_configured(settings):
        config = _signature(settings.seer_type, settings.seer_url, settings.seer_api_key, REQUESTS_INTERVAL)
        desired[RequestsWatcher.key] = (RequestsWatcher, REQUESTS_INTERVAL, config)
    return desired


class RealtimeSupervisor:
    def __init__(self, hub: RealtimeHub, board: StatusBoard) -> None:
        self.hub = hub
        self.board = board
        self.running = False
        self._tasks: dict[str, tuple[asyncio.Task[None], str]] = {}
        self._lock = asyncio.Lock()
        self._config_task: asyncio.Task[None] | None = None
        # Prochain essai permis par webhook en échec (horloge monotone).
        self._retry_at: dict[str, float] = {}

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

    async def apply(self, *, announce: bool = True, retry_webhooks: bool = False) -> None:
        if not self.running:
            return
        async with self._lock:
            snapshot = await run_in_threadpool(_load)
            settings = snapshot.settings
            desired = _desired(settings) if settings is not None else {}
            for key in [k for k in self._tasks if k not in desired or self._tasks[k][1] != desired[k][2]]:
                await self._stop(key)
            for key, (watcher_class, interval, config) in desired.items():
                if key not in self._tasks and settings is not None:
                    watcher = watcher_class(settings, interval, self.hub, self.board)
                    task = asyncio.create_task(watcher.run(), name=f"realtime-{key}")
                    self._tasks[key] = (task, config)
            await self._reconcile_webhooks(snapshot, retry=retry_webhooks)
        if announce:
            await live_events.publish({"type": "realtime.status"})

    async def _reconcile_webhooks(self, snapshot: _Snapshot, *, retry: bool) -> None:
        """Chaque instance Sonarr/Radarr reçoit son webhook ; une instance
        retirée perd sa ligne d'état."""
        wanted_keys = {w.key for w in snapshot.wanted}
        for status in self.board.snapshot():
            if status.kind == "webhook" and status.key not in wanted_keys:
                self.board.forget(status.key)
                self._retry_at.pop(status.key, None)
        now = time.monotonic()
        for wanted in snapshot.wanted:
            self.board.register(wanted.key, "webhook")
            if wanted.key in snapshot.connected:
                self._retry_at.pop(wanted.key, None)
                continue
            if not snapshot.base_url:
                self.board.failed(wanted.key, NO_ADDRESS)
                continue
            if not retry and self._retry_at.get(wanted.key, 0.0) > now:
                continue
            await self._register(wanted, snapshot.base_url)

    async def _register(self, wanted: webhooks.WantedWebhook, base_url: str) -> None:
        try:
            with Session(engine) as session:
                await webhooks.register(session, wanted.target, base_url, wanted.instance_id)
        except webhooks.WebhookError as exc:
            self._retry_at[wanted.key] = time.monotonic() + WEBHOOK_RETRY_SECONDS
            self.board.failed(wanted.key, str(exc))
            logger.warning("Webhook %s non branché : %s", wanted.key, exc)
        else:
            self._retry_at.pop(wanted.key, None)
            self.board.checked(wanted.key)

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
