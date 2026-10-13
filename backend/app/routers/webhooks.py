"""Réception des webhooks Sonarr/Radarr (temps réel, voir
services/realtime/webhooks.py).

Route PUBLIQUE (aucune session) : Sonarr/Radarr ne peuvent pas ouvrir de
session Analysarr. Elle est protégée autrement, et listée comme exception
dans `main.py` (préfixe `/api/webhooks/`, figé par tests/test_auth_routes.py) :
- secret propre à chaque instance, en authentification Basic, comparé en
  temps constant ; même 401 pour une instance inconnue ou un mauvais secret
  (rien n'indique ce qui existe) ;
- débit limité par adresse IP, corps limité à 64 Ko ;
- rien de ce que dit la charge n'est cru : seul l'identifiant du film ou de la
  série sert, la vérité est relue par l'API de Sonarr/Radarr.
La réponse part tout de suite : l'analyse se fait plus tard, dans la file."""

import json

from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app.database import engine
from app.models.arr_webhook import ArrWebhook
from app.services.rate_limit import retry_after
from app.services.realtime.hub import hub
from app.services.realtime.status import board
from app.services.realtime.targets import media_for_arr
from app.services.realtime.webhooks import (
    ARR_SERVICES,
    RESCAN_EVENTS,
    TEST_EVENT,
    find_row,
    parse_event,
    presented_secret,
    secret_matches,
    source_key,
)
from app.services.security import client_ip, load_trusted_proxies

router = APIRouter()

MAX_BODY_BYTES = 64 * 1024
# Un import de saison envoie un événement par épisode : large marge, mais une
# rafale anormale (boucle, abus) est coupée.
RATE_LIMIT = 300

_UNAUTHORIZED = HTTPException(401, "Webhook non autorisé.")


def _find(service: str, instance_id: int) -> ArrWebhook | None:
    with Session(engine) as session:
        row = find_row(session, service, instance_id)
        if row is not None:
            session.expunge(row)
        return row


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
        raise HTTPException(413, "Charge trop volumineuse.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "Charge trop volumineuse.")
    return bytes(body)


@router.post("/{service}/{instance_id}", status_code=204)
async def receive_webhook(service: str, instance_id: int, request: Request) -> Response:
    if service not in ARR_SERVICES or instance_id < 0:
        raise HTTPException(404, "Webhook inconnu.")
    ip = client_ip(request, await run_in_threadpool(load_trusted_proxies))
    # Compteur en mémoire, modifié dans la boucle uniquement (voir main.py).
    wait = retry_after(f"webhook:{ip or 'inconnu'}", limit=RATE_LIMIT)
    if wait is not None:
        raise HTTPException(429, "Trop de requêtes.", headers={"Retry-After": str(wait)})

    secret = presented_secret(request.headers.get("authorization"))
    row = await run_in_threadpool(_find, service, instance_id)
    if row is None or secret is None or not secret_matches(row, secret):
        raise _UNAUTHORIZED

    try:
        event = parse_event(service, json.loads(await _read_body(request) or b"null"))
    except ValueError as exc:
        raise HTTPException(400, "Charge illisible.") from exc
    if event is None:
        raise HTTPException(400, "Charge illisible.")

    board.event(source_key(service, instance_id))
    if event.event_type == TEST_EVENT or event.event_type not in RESCAN_EVENTS:
        return Response(status_code=204)
    if event.arr_id is None:
        # Événement sans film ni série identifiable : la liste du service est relue.
        hub.service_changed(service)
        return Response(status_code=204)
    media_id = await run_in_threadpool(media_for_arr, service, instance_id or None, event.arr_id)
    if media_id is None:
        # Nouveau film ou nouvelle série : seule l'analyse du service l'ajoute.
        hub.service_changed(service)
    else:
        hub.media_changed(media_id)
    return Response(status_code=204)
