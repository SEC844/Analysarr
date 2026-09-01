import asyncio
from typing import Any


class EventBroadcaster:
    """Pub/sub en mémoire pour diffuser la progression du scan à tous les clients SSE connectés."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    def subscribe(self) -> "asyncio.Queue[dict[str, Any]]":
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[dict[str, Any]]") -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            await queue.put(event)


scan_events = EventBroadcaster()
