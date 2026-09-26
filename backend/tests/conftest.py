"""Socle des tests : base SQLite jetable, client HTTP de l'application et faux
services externes (Emby/Jellyfin, Sonarr, Radarr, qBittorrent, Seer, GitHub).

Aucun test n'appelle un vrai service : toute requête sortante passe par
`fake_http`, qui route chaque hôte vers une fonction de test."""

import os
import sys
import tempfile

# Avant tout import de l'application : la base est ouverte à l'import.
_TMP_DIR = tempfile.mkdtemp(prefix="analysarr-tests-")
os.environ["DATABASE_PATH"] = os.path.join(_TMP_DIR, "analysarr.db")

# Racines réelles (non vides) : le garde-fou des montages refuse toute
# suppression quand la racine de la bibliothèque n'existe pas ou est vide
# (services/path_guard.py).
LIBRARY_PATH = os.path.join(_TMP_DIR, "media")
DOWNLOAD_PATH = os.path.join(_TMP_DIR, "torrents")
for _path in (LIBRARY_PATH, DOWNLOAD_PATH):
    os.makedirs(_path, exist_ok=True)
    open(os.path.join(_path, ".mounted"), "w").close()

from collections.abc import Callable  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

from app.database import engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.settings import Settings  # noqa: E402
from app.services import hardlink, rate_limit, service_status, updates  # noqa: E402

ADMIN = {"username": "admin", "password": "correct-horse-battery"}
Handler = Callable[[httpx.Request], httpx.Response]

_RealAsyncClient = httpx.AsyncClient


@pytest.fixture(autouse=True)
def fresh_database():
    SQLModel.metadata.drop_all(engine)
    init_db()
    updates._cache.clear()
    service_status._cache.clear()
    rate_limit.reset()  # compteur global en mémoire : chaque test repart à zéro


@pytest.fixture
def session():
    with Session(engine) as db:
        yield db


@pytest.fixture
def fake_http(monkeypatch) -> dict[str, Handler]:
    """`fake_http["http://emby"] = handler` : toutes les requêtes httpx
    asynchrones vers cet hôte sont servies par `handler`. Un hôte non déclaré
    se comporte comme un service injoignable."""
    routes: dict[str, Handler] = {}

    def dispatch(request: httpx.Request) -> httpx.Response:
        handler = routes.get(f"{request.url.scheme}://{request.url.host}")
        if handler is None:
            raise httpx.ConnectError("service injoignable", request=request)
        return handler(request)

    class RoutedAsyncClient(_RealAsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(dispatch)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", RoutedAsyncClient)
    return routes


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def login(client: TestClient, headers: dict[str, str] | None = None) -> httpx.Response:
    return client.post("/api/auth/login", json=ADMIN, headers=headers)


@pytest.fixture
def admin_client(client: TestClient) -> TestClient:
    assert client.post("/api/auth/setup", json=ADMIN).status_code == 201
    assert login(client).status_code == 200
    return client


@pytest.fixture
def settings(session: Session) -> Settings:
    row = Settings(
        id=1,
        media_server="emby",
        emby_url="http://emby",
        emby_api_key="emby-key",
        sonarr_url="http://sonarr",
        sonarr_api_key="sonarr-key",
        radarr_url="http://radarr",
        radarr_api_key="radarr-key",
        qbittorrent_url="http://qbit",
        qbittorrent_username="admin",
        qbittorrent_password="secret",
        emby_library_path=LIBRARY_PATH,
        qbittorrent_download_path=DOWNLOAD_PATH,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.fixture
def portable_inodes(monkeypatch):
    """Sous Windows, st_dev (numéro de série du volume) dépasse l'entier 64 bits
    signé de SQLite ; sous Linux, dans le conteneur, il reste petit. Les
    identifiants sont réduits sans changer lesquels sont égaux. Remplacé dans
    TOUS les modules qui l'importent, pour survivre à une réorganisation."""
    real = hardlink.stat_inode

    def small(path):
        found = real(path)
        return None if found is None else (found[0] % 2**62, found[1] % 2**31)

    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and getattr(module, "stat_inode", None) is real:
            monkeypatch.setattr(module, "stat_inode", small)
