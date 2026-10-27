"""Démarre, arrête et reconfigure le temps réel selon les services
configurés. Actif par défaut (`Settings.realtime_enabled`, Réglages →
Planification) : chaque service configuré est suivi (client torrent, serveur
multimédia, gestionnaire de demandes, chaque instance Sonarr/Radarr en
lecture seule) et chaque instance Sonarr/Radarr reçoit son webhook, sauf si
l'utilisateur les a désactivés (`Settings.arr_webhooks_enabled`). Désactivé,
plus rien n'est interrogé entre deux scans et les webhooks sont retirés de
chez Sonarr/Radarr.

Deux boucles indépendantes :
- les guetteurs (`apply`, toutes les `CONFIG_CHECK_SECONDS`) : lecture de la
  base seulement, jamais d'appel réseau — quel que soit l'écran qui change une
  adresse ou une clé, le guetteur concerné suit. Un guetteur n'est redémarré
  que si SA configuration a changé ;
- les webhooks (`reconcile_webhooks`, tâche à part) : branchement, retrait et
  vérification chez Sonarr/Radarr. JAMAIS pendant le démarrage : Sonarr/Radarr
  testent le webhook en appelant Analysarr, qui n'écoute qu'une fois démarré
  (bug réel : démarrage bloqué jusqu'au délai d'attente, webhook « injoignable »).
  Un essai refusé est retenté au bout de 1 min, 5 min puis toutes les 15 min,
  ou aussitôt sur demande. Un webhook branché est revérifié toutes les
  `WEBHOOK_VERIFY_SECONDS` (retiré à la main chez eux = rebranché).

Inactif tant que l'application n'a pas démarré (`start`) : les tests, qui
n'exécutent pas le cycle de vie, ne lancent jamais de tâche de fond."""

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app.clients.emby import media_server_client
from app.clients.torrent import torrent_client_configured
from app.database import engine
from app.models.settings import Settings
from app.services.arr_instances import ArrTarget
from app.services.events import live_events
from app.services.realtime import webhooks
from app.services.realtime.hub import RealtimeHub, hub
from app.services.realtime.status import STATUS_EVENT, StatusBoard, board
from app.services.realtime.watchers import (
    ARR_INTERVAL,
    MEDIA_SERVER_INTERVAL,
    REQUESTS_INTERVAL,
    TORRENT_INTERVAL,
    ArrWatcher,
    MediaServerWatcher,
    RequestsWatcher,
    TorrentWatcher,
    Watcher,
    arr_source_key,
)
from app.services.seer import seer_configured

logger = logging.getLogger(__name__)

CONFIG_CHECK_SECONDS = 5.0
# Délais avant un nouvel essai d'un webhook refusé : courts d'abord (Sonarr
# qui redémarre, réseau Docker qui se met en place), puis espacés.
WEBHOOK_RETRY_STEPS = (60.0, 300.0, 900.0)
WEBHOOK_VERIFY_SECONDS = 1800.0
NO_ADDRESS = "Adresse d'Analysarr inconnue : Réglages → Application."


@dataclass(frozen=True)
class _Snapshot:
    settings: Settings | None
    base_url: str
    # Webhooks voulus, et lesquels sont déjà branchés et à jour.
    wanted: list[webhooks.WantedWebhook]
    connected: set[str]
    # Webhooks qui ont une ligne ici (branchés, ou en cours de branchement).
    known: set[str]

    @property
    def realtime(self) -> bool:
        return self.settings is not None and self.settings.realtime_enabled

    @property
    def webhooks_wanted(self) -> bool:
        return self.realtime and self.settings is not None and self.settings.arr_webhooks_enabled


def _load() -> _Snapshot:
    with Session(engine) as session:
        settings = session.get(Settings, 1)
        wanted = webhooks.wanted_webhooks(session, settings)
        webhooks.forget_orphans(session, wanted)
        base_url = settings.analysarr_url if settings is not None else ""
        connected, known = set(), set()
        for w in wanted:
            row = webhooks.find_row(session, w.target.kind, w.instance_id)
            if row is not None:
                known.add(w.key)
            if webhooks.is_connected(row, w.target, base_url, w.instance_id):
                connected.add(w.key)
        if settings is not None:
            session.expunge(settings)
        return _Snapshot(settings, base_url, wanted, connected, known)


def _signature(*parts: object) -> str:
    return hashlib.sha256(repr(parts).encode()).hexdigest()


WatcherFactory = Callable[[Settings, RealtimeHub, StatusBoard], Watcher]


def _desired(snapshot: _Snapshot) -> dict[str, tuple[WatcherFactory, str]]:
    """Guetteurs voulus : (fabrique, empreinte de leur configuration)."""
    settings = snapshot.settings
    desired: dict[str, tuple[WatcherFactory, str]] = {}
    if settings is None or not snapshot.realtime:
        return desired
    if torrent_client_configured(settings):
        config = _signature(
            settings.torrent_client,
            settings.qbittorrent_url,
            settings.qbittorrent_username,
            settings.qbittorrent_password,
            TORRENT_INTERVAL,
        )
        desired[TorrentWatcher.key] = (lambda s, h, b: TorrentWatcher(s, TORRENT_INTERVAL, h, b), config)
    if media_server_client(settings) is not None:
        config = _signature(
            settings.media_server,
            settings.emby_url,
            settings.emby_api_key,
            settings.excluded_emby_user_ids,
            MEDIA_SERVER_INTERVAL,
        )
        desired[MediaServerWatcher.key] = (
            lambda s, h, b: MediaServerWatcher(s, MEDIA_SERVER_INTERVAL, h, b),
            config,
        )
    if seer_configured(settings):
        config = _signature(settings.seer_type, settings.seer_url, settings.seer_api_key, REQUESTS_INTERVAL)
        desired[RequestsWatcher.key] = (lambda s, h, b: RequestsWatcher(s, REQUESTS_INTERVAL, h, b), config)
    for wanted in snapshot.wanted:
        target = wanted.target
        config = _signature(target.kind, target.instance_id, target.url, target.api_key, ARR_INTERVAL)
        desired[arr_source_key(target.kind, wanted.instance_id)] = (_arr_watcher(target), config)
    return desired


def _arr_watcher(target: ArrTarget) -> WatcherFactory:
    def build(settings: Settings, hub: RealtimeHub, board: StatusBoard) -> Watcher:
        return ArrWatcher(settings, ARR_INTERVAL, hub, board, target)

    return build


class RealtimeSupervisor:
    def __init__(self, hub: RealtimeHub, board: StatusBoard) -> None:
        self.hub = hub
        self.board = board
        self.running = False
        self._tasks: dict[str, tuple[asyncio.Task[None], str]] = {}
        self._lock = asyncio.Lock()
        self._webhook_lock = asyncio.Lock()
        self._config_task: asyncio.Task[None] | None = None
        self._webhook_task: asyncio.Task[None] | None = None
        self._webhook_wake = asyncio.Event()
        self._retry_requested = False
        # Par webhook : prochain essai permis (horloge monotone), échecs
        # consécutifs, dernier message journalisé, dernière vérification.
        self._retry_at: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._logged: dict[str, str] = {}
        self._verified_at: dict[str, float] = {}

    async def start(self) -> None:
        """Rapide et sans appel réseau : le démarrage de l'application
        n'attend jamais un service extérieur."""
        self.running = True
        self.hub.start()
        await self.apply()
        self._config_task = asyncio.create_task(self._follow_config(), name="realtime-config")
        self._webhook_task = asyncio.create_task(self._follow_webhooks(), name="realtime-webhooks")

    async def stop(self) -> None:
        self.running = False
        for task in (self._config_task, self._webhook_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._config_task = self._webhook_task = None
        for key in list(self._tasks):
            await self._stop(key)
        await self.hub.stop()
        self.board.clear()

    async def apply(self, *, announce: bool = True) -> None:
        """Guetteurs selon la configuration enregistrée (base seulement)."""
        if not self.running:
            return
        async with self._lock:
            snapshot = await run_in_threadpool(_load)
            desired = _desired(snapshot)
            for key in [k for k in self._tasks if k not in desired or self._tasks[k][1] != desired[k][1]]:
                await self._stop(key)
            for key, (factory, config) in desired.items():
                if key not in self._tasks and snapshot.settings is not None:
                    watcher = factory(snapshot.settings, self.hub, self.board)
                    task = asyncio.create_task(watcher.run(), name=f"realtime-{key}")
                    self._tasks[key] = (task, config)
        if announce:
            await live_events.publish(STATUS_EVENT)

    # --- Webhooks -------------------------------------------------------------------

    def request_webhooks(self, *, retry: bool = False) -> None:
        """Demande une passe sur les webhooks sans l'attendre (nouvelle
        adresse, interrupteur changé)."""
        self._retry_requested = self._retry_requested or retry
        self._webhook_wake.set()

    async def reconcile_webhooks(self, *, retry: bool = False) -> None:
        if not self.running:
            return
        async with self._webhook_lock:
            await self._reconcile_webhooks(await run_in_threadpool(_load), retry=retry)

    async def _follow_webhooks(self) -> None:
        while True:
            # Première passe après `CONFIG_CHECK_SECONDS` : Analysarr écoute.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._webhook_wake.wait(), timeout=CONFIG_CHECK_SECONDS)
            self._webhook_wake.clear()
            retry, self._retry_requested = self._retry_requested, False
            try:
                await self.reconcile_webhooks(retry=retry)
            except Exception:  # noqa: BLE001 - la surveillance ne doit jamais s'arrêter
                logger.exception("Webhooks Sonarr/Radarr illisibles")

    async def _reconcile_webhooks(self, snapshot: _Snapshot, *, retry: bool) -> None:
        """Chaque instance Sonarr/Radarr reçoit son webhook (ou le perd s'ils
        sont désactivés) ; une instance retirée perd sa ligne d'état."""
        wanted_keys = {w.key for w in snapshot.wanted}
        for status in self.board.snapshot():
            if status.kind == "webhook" and status.key not in wanted_keys:
                self._forget(status.key)
        for wanted in snapshot.wanted:
            if not snapshot.webhooks_wanted:
                if wanted.key in snapshot.known:
                    await self._withdraw(wanted, retry=retry)
                else:
                    self._forget(wanted.key)
                continue
            self.board.register(wanted.key, "webhook")
            if wanted.key in snapshot.connected:
                self._succeeded(wanted.key)
                await self._verify(wanted)
                continue
            if not snapshot.base_url:
                self.board.failed(wanted.key, NO_ADDRESS)
                continue
            if self._may_try(wanted.key, retry):
                await self._register(wanted, snapshot.base_url)

    def _may_try(self, key: str, retry: bool) -> bool:
        return retry or self._retry_at.get(key, 0.0) <= time.monotonic()

    async def _register(self, wanted: webhooks.WantedWebhook, base_url: str) -> None:
        try:
            with Session(engine) as session:
                await webhooks.register(session, wanted.target, base_url, wanted.instance_id)
        except webhooks.WebhookError as exc:
            self._failed(wanted.key, f"Webhook {wanted.key} non branché", str(exc))
        else:
            self._succeeded(wanted.key)
            self._verified_at[wanted.key] = time.monotonic()
            self.board.checked(wanted.key)

    async def _withdraw(self, wanted: webhooks.WantedWebhook, *, retry: bool) -> None:
        """Webhooks désactivés (ou temps réel coupé) : celui d'Analysarr est
        retiré de chez Sonarr/Radarr. En échec, il reste listé avec la raison
        et le retrait est retenté."""
        with Session(engine) as session:
            row = webhooks.find_row(session, wanted.target.kind, wanted.instance_id)
            if row is None:
                self._forget(wanted.key)
                return
            if not self._may_try(wanted.key, retry):
                return
            try:
                await webhooks.unregister(session, wanted.target, row)
            except webhooks.WebhookError as exc:
                self.board.register(wanted.key, "webhook")
                self._failed(wanted.key, f"Webhook {wanted.key} non retiré", f"Retrait impossible : {exc}")
                return
        logger.info("Webhook %s retiré de %s", wanted.key, wanted.target.name)
        self._forget(wanted.key)

    async def _verify(self, wanted: webhooks.WantedWebhook) -> None:
        """Webhook retiré à la main dans Sonarr/Radarr : oublié ici, la passe
        suivante le rebranche. Une erreur réseau ne prouve rien."""
        now = time.monotonic()
        last = self._verified_at.get(wanted.key)
        if last is not None and now - last < WEBHOOK_VERIFY_SECONDS:
            return
        self._verified_at[wanted.key] = now
        with Session(engine) as session:
            row = webhooks.find_row(session, wanted.target.kind, wanted.instance_id)
            if row is None:
                return
            try:
                present = await webhooks.still_registered(wanted.target, row)
            except httpx.HTTPError:
                logger.debug("Vérification du webhook %s impossible", wanted.key, exc_info=True)
                return
            if present:
                self.board.checked(wanted.key)
                return
            row.notification_id = None
            session.add(row)
            session.commit()
        logger.info("Webhook %s absent de %s : rebranchement", wanted.key, wanted.target.name)
        self.request_webhooks(retry=True)

    def _failed(self, key: str, context: str, message: str) -> None:
        failures = self._failures.get(key, 0)
        self._failures[key] = failures + 1
        delay = WEBHOOK_RETRY_STEPS[min(failures, len(WEBHOOK_RETRY_STEPS) - 1)]
        self._retry_at[key] = time.monotonic() + delay
        self.board.failed(key, message)
        # Une panne qui dure n'écrit qu'une ligne, pas une par essai.
        if self._logged.get(key) != message:
            self._logged[key] = message
            logger.warning("%s : %s", context, message)

    def _succeeded(self, key: str) -> None:
        self._retry_at.pop(key, None)
        self._failures.pop(key, None)
        self._logged.pop(key, None)

    def _forget(self, key: str) -> None:
        self._succeeded(key)
        self._verified_at.pop(key, None)
        self.board.forget(key)

    # --- Guetteurs ------------------------------------------------------------------

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
