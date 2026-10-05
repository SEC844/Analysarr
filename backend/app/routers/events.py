"""Flux permanent des événements de la bibliothèque (temps réel) : le
navigateur s'y abonne une fois pour toute l'application et relit ce qui a
changé (voir services/realtime/hub.py). Route authentifiée."""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.events import live_events
from app.sse import sse_response

router = APIRouter()


@router.get("/stream")
async def events_stream() -> StreamingResponse:
    return sse_response(live_events)
