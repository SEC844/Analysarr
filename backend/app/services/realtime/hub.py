"""File du temps réel : les sources (webhooks Sonarr/Radarr, client torrent,
serveur multimédia) signalent « ce média a changé » ; la file regroupe, puis
réanalyse avec le code existant et prévient le navigateur.

- Média désigné par sa clé stable (`ignores.media_key`), résolue en
  identifiant APRÈS la prise du verrou : un scan complet régénère les
  identifiants pendant qu'un signal attend (bug réel évité : le mauvais média
  était relu). Une clé qui ne désigne plus rien est simplement ignorée.
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
reste dans l'état de la dernière analyse réussie. Le lot rend le verrou
seulement quand TOUTES ses analyses sont finies, même si l'une échoue (bug réel
possible : une erreur imprévue, « database is locked », faisait sortir le lot
du verrou pendant qu'une autre analyse écrivait encore). Un média en échec est
remis en file après `RETRY_DELAYS`, et le retard s'affiche dans l'indicateur du
temps réel (source `analysis`) ; au-delà, le scan de nuit le rattrape."""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app.database import engine
from app.models.media import Media
from app.models.settings import Settings
from app.services.events import live_events
from app.services.realtime.status import board
from app.services.scan_scopes import SERVICE_SCOPES

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE = 3.0
# Deux analyses d'un même service (tous les torrents, toute la bibliothèque)
# sont espacées d'au moins une minute : elles coûtent bien plus qu'un média.
SERVICE_COOLDOWN = 60.0
MAX_PARALLEL = 2
# Nouvel essai d'un média dont l'analyse a échoué ; au-delà, abandon (le scan
# de nuit le rattrapera).
RETRY_DELAYS = (30.0, 120.0, 600.0)
ANALYSIS_SOURCE = "analysis"


@dataclass
class _Pending:
    first_seen: float
    full: bool


class RealtimeHub:
    def __init__(self) -> None:
        self.debounce = DEFAULT_DEBOUNCE
        self._media: dict[str, _Pending] = {}
        self._services: dict[str, float] = {}
        self._service_last_run: dict[str, float] = {}
        # Échecs consécutifs par média, et la dernière erreur vue.
        self._attempts: dict[str, int] = {}
        self._last_error = ""
        self._unaddressable_logged: set[str] = set()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    # --- Signaux -------------------------------------------------------------

    def media_changed(self, media_key: str, *, watch_only: bool = False) -> None:
        # Import différé : ignores → scan.statuses → queue_issues → hub.
        from app.services.ignores import is_addressable

        if not is_addressable(media_key):
            # Clé partagée par plusieurs médias (ni fiche Sonarr/Radarr ni
            # item du serveur multimédia) : la relire reviendrait à relire un
            # média au hasard. Le scan complet s'en charge.
            if media_key not in self._unaddressable_logged:
                self._unaddressable_logged.add(media_key)
                logger.warning("Temps réel : média sans identité stable ignoré (%s), relu au prochain scan", media_key)
            return
        pending = self._media.get(media_key)
        if pending is None:
            self._media[media_key] = _Pending(time.monotonic(), full=not watch_only)
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

    def _take_due(self) -> tuple[dict[str, bool], list[str]]:
        now = time.monotonic()
        media = {key: p.full for key, p in self._media.items() if p.first_seen + self.debounce <= now}
        for key in media:
            del self._media[key]
        services = [scope for scope, seen in self._services.items() if self._due_at(scope, seen) <= now]
        for scope in services:
            del self._services[scope]
            # Délai compté dès le départ de l'analyse : une demande arrivée
            # pendant qu'elle tourne attend son tour.
            self._service_last_run[scope] = now
        return media, services

    # --- Traitement ------------------------------------------------------------

    async def process(self, media: dict[str, bool], services: list[str]) -> None:
        """Analyse un lot (clé du média → analyse complète ou visionnage seul)."""
        # Imports différés : le moteur de scan importe des modules qui
        # dépendent de celui-ci.
        from app.services.partial_scan import run_service_scan
        from app.services.realtime.targets import media_ids_for_keys
        from app.services.realtime.webhooks import confirm_deliveries
        from app.services.scan import short_analysis

        if not media and not services:
            return
        async with short_analysis():
            for scope in services:
                await run_service_scan(scope, trigger="realtime")
                await live_events.publish({"type": "library.changed", "scope": scope})
            # Résolu verrou pris : aucun scan ne peut plus renuméroter les médias.
            ids = await run_in_threadpool(media_ids_for_keys, list(media))
            semaphore = asyncio.Semaphore(MAX_PARALLEL)

            async def one(key: str, full: bool) -> bool:
                media_id = ids.get(key)
                if media_id is None:
                    # Retiré entre-temps (scan, suppression) : son ancien
                    # identifiant est inconnu, le navigateur relit ses listes.
                    await live_events.publish({"type": "library.changed", "scope": "media"})
                    return True
                async with semaphore:
                    return await self._analyse(media_id, full)

            ordered = sorted(media.items())
            # `return_exceptions` : le verrou n'est rendu qu'une fois TOUTES les
            # analyses finies, même quand l'une d'elles échoue.
            results = await asyncio.gather(*(one(mid, full) for mid, full in ordered), return_exceptions=True)
        changed = False
        for (key, full), result in zip(ordered, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                self._failed(key, full, result)
            else:
                self._attempts.pop(key, None)
                # Événements reçus par webhook : livrés maintenant seulement.
                confirm_deliveries(key)
                changed = changed or result
        self._report_delays()
        if services or changed:
            await live_events.publish({"type": "cleanup.changed"})

    def _failed(self, media_id: str, full: bool, exc: BaseException) -> None:
        """Analyse en échec : remise en file après un délai croissant, puis
        abandon (le scan de nuit relira le média)."""
        attempt = self._attempts.get(media_id, 0) + 1
        self._last_error = f"{type(exc).__name__} : {exc}"[:200]
        if attempt > len(RETRY_DELAYS):
            self._attempts.pop(media_id, None)
            logger.warning("Analyse en temps réel du média %s abandonnée : %s", media_id, self._last_error)
            return
        self._attempts[media_id] = attempt
        delay = RETRY_DELAYS[attempt - 1]
        logger.warning(
            "Analyse en temps réel du média %s impossible, nouvel essai dans %.0f s", media_id, delay, exc_info=exc
        )
        pending = self._media.get(media_id)
        if pending is None:
            # Échéance = first_seen + debounce : décalée pour tomber après le délai.
            self._media[media_id] = _Pending(time.monotonic() + delay - self.debounce, full=full)
        else:
            pending.full = pending.full or full
        self._wake.set()

    def _report_delays(self) -> None:
        """Analyses en retard affichées dans l'indicateur du temps réel."""
        if not self._attempts:
            board.forget(ANALYSIS_SOURCE)
            return
        board.register(ANALYSIS_SOURCE, "analysis")
        count = len(self._attempts)
        board.failed(
            ANALYSIS_SOURCE,
            f"{count} média(s) en attente d'une nouvelle analyse (dernière erreur : {self._last_error}).",
        )

    async def _analyse(self, media_id: int, full: bool) -> bool:
        """True si le média a été relu (ou retiré)."""
        from app.services.media_rescan import rescan_media
        from app.services.watch_stats import refresh_media_watch

        # Toute erreur remonte jusqu'au lot (`process`), qui la journalise et
        # remet le média en file.
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
        event = "media.removed" if result.media_deleted else "media.updated"
        await live_events.publish({"type": event, "media_id": media_id})
        return True


hub = RealtimeHub()
