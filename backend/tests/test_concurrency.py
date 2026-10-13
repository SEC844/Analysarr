"""Charge simultanée sur un vrai serveur uvicorn.

Bug réel : derrière un reverse-proxy, l'affichage de la bibliothèque déclenche
une salve de requêtes de jaquettes, et l'application se figeait jusqu'au
redémarrage du service — `/api/health` compris. Le middleware d'authentification
faisait son travail de base synchrone DANS la boucle asyncio, en attendant une
connexion du pool, pendant que les requêtes qui détenaient ces connexions ne
pouvaient plus rendre leur réponse, faute de boucle libre : interblocage.

Ces tests démarrent uvicorn dans un thread (l'application et la base de test
sont celles de conftest.py) : TestClient ne passe pas par une vraie boucle de
serveur et ne reproduit pas le blocage."""

import asyncio
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn

from app.main import app
from tests.conftest import ADMIN

REQUESTS = 150
ALL_DONE_WITHIN = 10.0
HEALTH_WITHIN = 1.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def live_server() -> Iterator[str]:
    port = _free_port()
    # Sans lifespan : la base est déjà créée par conftest, et le planificateur
    # n'a rien à faire ici.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn n'a pas démarré")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    # Serveur figé : le thread est un démon, il ne retient pas la fin des tests.
    thread.join(timeout=5)


def _session_cookies(base_url: str) -> httpx.Cookies:
    with httpx.Client(base_url=base_url) as client:
        assert client.post("/api/auth/setup", json=ADMIN).status_code == 201
        response = client.post("/api/auth/login", json=ADMIN)
        assert response.status_code == 200
        return response.cookies


async def _burst(base_url: str, cookies: httpx.Cookies) -> list[int]:
    reads = [
        "/api/scan/history",
        "/api/library/history",
        "/api/library/forecast",
        "/api/seed-protection",
        "/api/cleanup/candidates",
        "/api/realtime/status",
    ]
    paths = [f"/api/media/{i}/poster" if i % 2 else reads[i // 2 % len(reads)] for i in range(REQUESTS)]
    limits = httpx.Limits(max_connections=REQUESTS, max_keepalive_connections=REQUESTS)
    async with httpx.AsyncClient(base_url=base_url, cookies=cookies, limits=limits, timeout=30) as client:
        responses = await asyncio.wait_for(
            asyncio.gather(*(client.get(path) for path in paths)), timeout=ALL_DONE_WITHIN
        )
    return [response.status_code for response in responses]


def test_a_burst_of_authenticated_requests_never_freezes_the_app(live_server):
    cookies = _session_cookies(live_server)

    started = time.monotonic()
    statuses = asyncio.run(_burst(live_server, cookies))
    elapsed = time.monotonic() - started

    # Aucun média en base : les jaquettes répondent 404, les lectures 200.
    assert set(statuses) <= {200, 404}, statuses
    assert statuses.count(200) == REQUESTS // 2
    print(f"{REQUESTS} requêtes simultanées en {elapsed:.2f} s")

    started = time.monotonic()
    health = httpx.get(f"{live_server}/api/health", timeout=HEALTH_WITHIN)
    assert health.status_code == 200
    assert time.monotonic() - started < HEALTH_WITHIN
