"""Flux Server-Sent Events compatibles reverse-proxy, partagés par la
progression du scan et les événements de la bibliothèque.

- Premier octet immédiat (`: connected`) : sans rien à envoyer avant le
  premier événement, un proxy qui met la réponse en tampon ne transmet jamais
  l'ouverture du flux — l'interface restait bloquée sur « Démarrage du
  scan... ».
- `: ping` toutes les 15 s : un proxy ou un navigateur ne ferme pas un flux
  silencieux, et une connexion morte est détectée.
- `no-transform` + `X-Accel-Buffering: no` : ni compression ni mise en tampon
  par un proxy (nginx, Nginx Proxy Manager, SWAG...)."""

import asyncio
import json
from collections.abc import AsyncIterator, Collection

from fastapi.responses import StreamingResponse

from app.services.events import EventBroadcaster

HEARTBEAT_SECONDS = 15


def sse_response(broadcaster: EventBroadcaster, *, stop_on: Collection[str] = ()) -> StreamingResponse:
    """Diffuse les événements de `broadcaster` ; le flux se termine sur un
    événement dont le type figure dans `stop_on` (fin d'un scan)."""
    queue = broadcaster.subscribe()

    async def stream() -> AsyncIterator[str]:
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event.get("type") in stop_on:
                    break
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
