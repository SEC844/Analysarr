"""File du temps réel : les sources (webhooks Sonarr/Radarr, client torrent,
serveur multimédia) signalent « ce média a changé » ; la file regroupe, puis
réanalyse avec le code existant et prévient le navigateur.

- Regroupement par média : un média signalé plusieurs fois dans la fenêtre
  (`debounce`, 3 s par défaut) n'est analysé qu'une fois — un pack de saison
  importé épisode par épisode = une seule analyse. La fenêtre part du PREMIER
  signal et n'est jamais prolongée : un média qui change sans cesse est tout
  de même analysé au plus tard `debounce` secondes après.
- Analyse complète (`rescan_media`) ou simple rafraîchissement du visionnage
  (`refresh_media_watch`, bien plus léger) ; la première l'emporte.
- Élément inconnu (nouveau film, nouveau torrent) : analyse du service
  concerné, au plus une fois par `SERVICE_COOLDOWN` (le reste du temps la
  demande attend, jamais perdue).
- Chaque lot tient le verrou d'analyse (`scan.short_analysis`) : jamais en
  même temps qu'un scan, qui attend la fin du lot ; un lot qui arrive pendant
  un scan attend la fin du scan.
- Deux médias au plus analysés en parallèle.

Aucune erreur d'une source ne remonte : elle est journalisée et le média
reste dans l'état de la dernière analyse réussie."""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

import httpx
from sqlmodel import Session

from app.database import engine
from app.models.media import Media
from app.models.settings import Settings
from app.services.events import live_events
from app.services.scan_scopes import SERVICE_SCOPES

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE = 3.0
# Deux analyses d'un même service (tous les torrents, toute la bibliothèque)
# sont espacées d'au moins une minute : elles coûtent bien plus qu'un média.
SERVICE_COOLDOWN = 60.0
MAX_PARALLEL = 2


@dataclass
class _Pending:
    first_seen: float
    full: bool


class RealtimeHub:
    def __init__(self) -> None:
        self.debounce = DEFAULT_DEBOUNCE
        self._media: dict[int, _Pending] = {}
        self._services: dict[str, float] = {}
        self._service_last_run: dict[str, float] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    # --- Signaux -------------------------------------------------------------

    def media_changed(self, media_id: int, *, watch_only: bool = False) -> None:
        pending = self._media.get(media_id)
        if pending is None:
            self._media[media_id] = _Pending(time.monotonic(), full=not watch_only)
        else:
            pending.full = pending.full or not watch_only
        self._wake.set()

    def service_changed(self, scope: str) -> None:
        if scope not in SERVICE_SCOPES:
            raise ValueError(f"Périmètre d'analyse inconnu : {scope}")
        self._services.setdefault(scope, time.monotonic())
        self._wake.set()

    @property
    def pending_count(self) -> int:
        return len(self._media) + len(self._services)

    # --- Boucle ----------------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="realtime-hub")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        while True:
            delay = self._next_delay()
            self._wake.clear()
            if delay is None:
                await self._wake.wait()
                continue
            if delay > 0:
                # Réveil anticipé par un nouveau signal, ou échéance atteinte.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                continue
            media, services = self._take_due()
            try:
                await self.process(media, services)
            except Exception:  # noqa: BLE001 - la boucle ne doit jamais s'arrêter
                logger.exception("Lot du temps réel en échec")

    def _due_at(self, scope: str, first_seen: float) -> float:
        last = self._service_last_run.get(scope)
        due = first_seen + self.debounce
        return due if last is None else max(due, last + SERVICE_COOLDOWN)

    def _next_delay(self) -> float | None:
        deadlines = [p.first_seen + self.debounce for p in self._media.values()]
        deadlines += [self._due_at(scope, seen) for scope, seen in self._services.items()]
        return max(0.0, min(deadlines) - time.monotonic()) if deadlines else None

    def _take_due(self) -> tuple[dict[int, bool], list[str]]:
        now = time.monotonic()
        media = {mid: p.full for mid, p in self._media.items() if p.first_seen + self.debounce <= now}
        for media_id in media:
            del self._media[media_id]
        services = [scope for scope, seen in self._services.items() if self._due_at(scope, seen) <= now]
        for scope in services:
            del self._services[scope]
            # Délai compté dès le départ de l'analyse : une demande arrivée
            # pendant qu'elle tourne attend son tour.
            self._service_last_run[scope] = now
        return media, services

    # --- Traitement ------------------------------------------------------------

    async def process(self, media: dict[int, bool], services: list[str]) -> None:
        """Analyse un lot (média → analyse complète ou visionnage seul)."""
        # Imports différés : le moteur de scan importe des modules qui
        # dépendent de celui-ci.
        from app.services.partial_scan import run_service_scan
        from app.services.scan import short_analysis

        if not media and not services:
            return
        async with short_analysis():
            for scope in services:
                await run_service_scan(scope, trigger="realtime")
                await live_events.publish({"type": "library.changed", "scope": scope})
            semaphore = asyncio.Semaphore(MAX_PARALLEL)

            async def one(media_id: int, full: bool) -> bool:
                async with semaphore:
                    return await self._analyse(media_id, full)

            changed = await asyncio.gather(*(one(mid, full) for mid, full in sorted(media.items())))
        if services or any(changed):
            await live_events.publish({"type": "cleanup.changed"})

    async def _analyse(self, media_id: int, full: bool) -> bool:
        """True si le média a été relu (ou retiré)."""
        from app.services.media_rescan import rescan_media
        from app.services.watch_stats import refresh_media_watch

        try:
            with Session(engine) as session:
                media = session.get(Media, media_id)
                if media is None:
                    # Retiré entre-temps (scan, suppression) : rien à relire.
                    await live_events.publish({"type": "media.removed", "media_id": media_id})
                    return True
                settings = session.get(Settings, 1)
                if settings is None:
                    return False
                if not full:
                    await refresh_media_watch(session, media, settings)
                    await live_events.publish({"type": "media.updated", "media_id": media_id, "watch": True})
                    return False
                result = await rescan_media(session, settings, media)
        except (httpx.HTTPError, RuntimeError, ValueError):
            logger.warning("Analyse en temps réel du média %s impossible", media_id, exc_info=True)
            return False
        event = "media.removed" if result.media_deleted else "media.updated"
        await live_events.publish({"type": event, "media_id": media_id})
        return True


hub = RealtimeHub()
