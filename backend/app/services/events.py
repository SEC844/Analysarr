import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Événement envoyé à un abonné qui a pris trop de retard : ses événements en
# attente sont abandonnés et il relit tout (voir `EventBroadcaster.publish`).
RESYNC_EVENT: dict[str, Any] = {"type": "resync"}


class EventBroadcaster:
    """Pub/sub en mémoire vers les clients SSE connectés.

    `maxsize` borne la file de chaque abonné : un navigateur lent (onglet en
    arrière-plan, réseau saturé) ne fait jamais grossir la mémoire du serveur.
    Quand sa file est pleine, elle est vidée et remplacée par un seul
    `resync` — l'abonné ne sait plus ce qu'il a manqué, il relit tout. Sans
    borne (`maxsize=0`, progression d'un scan), rien n'est jamais perdu."""

    def __init__(self, maxsize: int = 0) -> None:
        self._maxsize = maxsize
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    def subscribe(self) -> "asyncio.Queue[dict[str, Any]]":
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self._maxsize)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[dict[str, Any]]") -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("Abonné SSE en retard : resynchronisation demandée")
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(RESYNC_EVENT)


scan_events = EventBroadcaster()

# Événements de la bibliothèque, pour toute l'application (temps réel, voir
# services/realtime/) : `media.updated`, `media.removed`, `library.changed`,
# `cleanup.changed`, `realtime.status`.
live_events = EventBroadcaster(maxsize=200)
