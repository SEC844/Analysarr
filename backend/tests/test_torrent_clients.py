import asyncio
import json

import httpx
import pytest

from app.clients.deluge import DelugeClient
from app.clients.qbittorrent import QbittorrentClient
from app.clients.torrent import torrent_client, torrent_client_configured, torrent_client_name
from app.clients.torrent_base import TorrentAuthError
from app.clients.transmission import TransmissionClient
from app.schemas.settings import ConnectionTestRequest
# Alias : un nom commençant par test_ serait collecté comme test par pytest.
from app.services.connection_test import test_torrent_client as check_torrent_client

DELUGE_TORRENT = {
    "name": "Matrix.1999.1080p",
    "download_location": "/data/torrents/complete",
    "total_size": 100,
    "ratio": 1.5,
    "num_seeds": 3,
    "num_peers": 1,
    "time_added": 1_700_000_000,
    "completed_time": 1_700_000_100,
    "label": "films",
    "files": [{"path": "Matrix.1999.1080p/matrix.mkv", "size": 100}],
    "trackers": [{"url": "https://tracker.example/announce"}],
}

TRANSMISSION_TORRENT = {
    "hashString": "ABC123",
    "name": "Matrix.1999.1080p",
    "downloadDir": "/data/torrents/complete",
    "totalSize": 100,
    "uploadRatio": 1.5,
    "peersSendingToUs": 3,
    "peersGettingFromUs": 1,
    "addedDate": 1_700_000_000,
    "doneDate": 1_700_000_100,
    "files": [{"name": "Matrix.1999.1080p/matrix.mkv", "length": 100}],
    "trackers": [{"announce": "https://tracker.example/announce"}],
    "labels": ["films"],
}


def deluge_handler(calls: list[tuple[str, list]], connected: bool = True, login: bool = True):
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append((body["method"], body["params"]))
        results = {
            "auth.login": login,
            "web.connected": connected,
            "web.get_hosts": [["host-id", "127.0.0.1", 58846, "localclient"]],
            "web.connect": None,
            "core.get_torrents_status": {"abc123": DELUGE_TORRENT},
            "core.remove_torrents": [],
        }
        return httpx.Response(200, json={"id": body["id"], "result": results.get(body["method"]), "error": None})

    return handler


def transmission_handler(calls: list[dict], session_id: str | None = "session-1", status: int = 200):
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append({"method": body["method"], "arguments": body["arguments"], "session": req.headers.get("X-Transmission-Session-Id")})
        if status != 200:
            return httpx.Response(status)
        if session_id and req.headers.get("X-Transmission-Session-Id") != session_id:
            return httpx.Response(409, headers={"X-Transmission-Session-Id": session_id})
        arguments = {"torrents": [TRANSMISSION_TORRENT]} if body["method"] == "torrent-get" else {}
        return httpx.Response(200, json={"result": "success", "arguments": arguments})

    return handler


def test_deluge_returns_the_common_torrent_shape(fake_http):
    calls: list[tuple[str, list]] = []
    fake_http["http://deluge"] = deluge_handler(calls)

    async def run():
        async with DelugeClient("http://deluge", "secret") as client:
            torrents = await client.get_torrents()
            files = await client.get_files("abc123")
            trackers = await client.get_trackers("abc123")
            await client.delete_torrents(["abc123"], delete_files=True)
        return torrents, files, trackers

    torrents, files, trackers = asyncio.run(run())

    [torrent] = torrents
    assert torrent["hash"] == "abc123" and torrent["name"] == "Matrix.1999.1080p"
    assert torrent["save_path"] == "/data/torrents/complete"
    assert torrent["content_path"].replace("\\", "/") == "/data/torrents/complete/Matrix.1999.1080p"
    assert (torrent["size"], torrent["ratio"], torrent["num_seeds"], torrent["num_leechs"]) == (100, 1.5, 3, 1)
    assert (torrent["added_on"], torrent["completion_on"], torrent["category"]) == (1_700_000_000, 1_700_000_100, "films")
    assert files == [{"name": "Matrix.1999.1080p/matrix.mkv", "size": 100}]
    assert trackers == [{"url": "https://tracker.example/announce", "status": None}]
    # Un seul appel de statuts sert aussi aux fichiers et aux trackers.
    assert [method for method, _ in calls].count("core.get_torrents_status") == 1
    assert ("core.remove_torrents", [["abc123"], True]) in calls


def test_deluge_connects_the_web_ui_to_its_daemon(fake_http):
    calls: list[tuple[str, list]] = []
    fake_http["http://deluge"] = deluge_handler(calls, connected=False)

    async def run():
        async with DelugeClient("http://deluge", "secret") as client:
            return await client.get_torrents()

    assert len(asyncio.run(run())) == 1
    assert ("web.connect", ["host-id"]) in calls


def test_deluge_refuses_a_wrong_password(fake_http):
    fake_http["http://deluge"] = deluge_handler([], login=False)

    async def run():
        async with DelugeClient("http://deluge", "wrong"):
            pass

    with pytest.raises(TorrentAuthError):
        asyncio.run(run())


def test_transmission_replays_the_session_token(fake_http):
    calls: list[dict] = []
    fake_http["http://transmission"] = transmission_handler(calls)

    async def run():
        async with TransmissionClient("http://transmission", None, None) as client:
            torrents = await client.get_torrents()
            files = await client.get_files("abc123")
            await client.delete_torrents(["ABC123"], delete_files=True)
        return torrents, files

    torrents, files = asyncio.run(run())

    [torrent] = torrents
    assert torrent["hash"] == "ABC123" and torrent["category"] == "films"
    assert torrent["content_path"].replace("\\", "/") == "/data/torrents/complete/Matrix.1999.1080p"
    assert (torrent["num_seeds"], torrent["num_leechs"], torrent["ratio"]) == (3, 1, 1.5)
    assert files == [{"name": "Matrix.1999.1080p/matrix.mkv", "size": 100}]
    # Première requête sans jeton, rejouée avec celui renvoyé par le 409.
    assert calls[0]["session"] == "" and calls[1]["session"] == "session-1"
    assert calls[-1] == {
        "method": "torrent-remove",
        "arguments": {"ids": ["ABC123"], "delete-local-data": True},
        "session": "session-1",
    }


def test_transmission_reports_refused_credentials(fake_http):
    fake_http["http://transmission"] = transmission_handler([], status=401)

    async def run():
        async with TransmissionClient("http://transmission", "admin", "wrong"):
            pass

    with pytest.raises(TorrentAuthError):
        asyncio.run(run())


@pytest.mark.parametrize(
    ("kind", "username", "password", "configured", "client_type"),
    [
        ("qbittorrent", "admin", "secret", True, QbittorrentClient),
        ("qbittorrent", None, "secret", False, QbittorrentClient),
        ("deluge", None, "secret", True, DelugeClient),
        ("deluge", None, None, False, DelugeClient),
        ("transmission", None, None, True, TransmissionClient),
    ],
)
def test_each_client_declares_its_own_required_credentials(settings, kind, username, password, configured, client_type):
    settings.torrent_client = kind
    settings.qbittorrent_username, settings.qbittorrent_password = username, password

    assert torrent_client_configured(settings) is configured
    assert isinstance(torrent_client(settings), client_type)
    assert torrent_client_name(settings) == {"qbittorrent": "qBittorrent", "deluge": "Deluge", "transmission": "Transmission"}[kind]


def test_connection_test_covers_every_client(fake_http):
    fake_http["http://deluge"] = deluge_handler([])
    fake_http["http://transmission"] = transmission_handler([])

    deluge = asyncio.run(
        check_torrent_client(ConnectionTestRequest(url="http://deluge/", password="secret", torrent_client="deluge"))
    )
    transmission = asyncio.run(
        check_torrent_client(ConnectionTestRequest(url="http://transmission", torrent_client="transmission"))
    )
    unreachable = asyncio.run(
        check_torrent_client(ConnectionTestRequest(url="http://nowhere", password="x", torrent_client="deluge"))
    )

    assert deluge.success and "Deluge" in deluge.message
    assert transmission.success and "Transmission" in transmission.message
    assert not unreachable.success and "Connexion impossible" in unreachable.message
