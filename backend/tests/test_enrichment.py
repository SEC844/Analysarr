"""Données enrichies (roadmap Phase 1) : torrents privés selon le client et
sa version, favoris et activité des comptes sur Emby comme sur Jellyfin,
statut des séries Sonarr."""

import asyncio

import httpx
import pytest
from sqlmodel import select

from app.clients.deluge import DelugeClient
from app.clients.emby import EmbyClient
from app.clients.qbittorrent import QbittorrentClient
from app.clients.transmission import TransmissionClient
from app.models.media import EmbyUser, Media, MediaType, Torrent
from app.services import watch_stats
from app.services.scan.results import series_status
from app.services.torrent_match import fetch_torrents, read_private_flag
from tests import test_media_server as servers
from tests.test_torrent_clients import DELUGE_TORRENT, TRANSMISSION_TORRENT, deluge_handler, transmission_handler

# --- Torrents privés ------------------------------------------------------------


def qbit_server(info: list[dict], properties: dict[str, dict], calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        path = request.url.path
        if path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=abc; path=/"})
        if path == "/api/v2/torrents/info":
            return httpx.Response(200, json=info)
        if path == "/api/v2/torrents/properties":
            return httpx.Response(200, json=properties.get(request.url.params["hash"], {}))
        return httpx.Response(200, json=[])

    return handler


def _qbit_flags(fake_http, info, properties=None):
    calls: list[str] = []
    fake_http["http://qbit"] = qbit_server(info, properties or {}, calls)

    async def run():
        async with QbittorrentClient("http://qbit", "admin", "secret") as client:
            torrents = await client.get_torrents()
            return [await read_private_flag(client, t) for t in torrents]

    return asyncio.run(run()), calls


def test_qbittorrent_5_publishes_private_in_the_list(fake_http):
    info = [{"hash": "a", "private": True}, {"hash": "b", "private": False}, {"hash": "c", "private": None}]

    flags, calls = _qbit_flags(fake_http, info)

    # null = métadonnées absentes : inconnu. Aucun appel en plus.
    assert flags == [True, False, None]
    assert "/api/v2/torrents/properties" not in calls


def test_qbittorrent_4_6_reads_the_properties(fake_http):
    info = [{"hash": "a"}, {"hash": "b"}]

    flags, calls = _qbit_flags(fake_http, info, {"a": {"is_private": True}, "b": {"is_private": False}})

    assert flags == [True, False]
    assert calls.count("/api/v2/torrents/properties") == 2


def test_qbittorrent_before_4_5_1_stops_asking_after_the_first_answer(fake_http):
    info = [{"hash": "a"}, {"hash": "b"}, {"hash": "c"}]

    flags, calls = _qbit_flags(fake_http, info, {"a": {"save_path": "/x"}})

    assert flags == [None, None, None]
    assert calls.count("/api/v2/torrents/properties") == 1


def test_an_unreadable_flag_is_unknown_never_an_error(fake_http, caplog):
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=abc; path=/"})
        return httpx.Response(500)

    fake_http["http://qbit"] = broken

    async def run():
        async with QbittorrentClient("http://qbit", "admin", "secret") as client:
            return await read_private_flag(client, {"hash": "a"})

    assert asyncio.run(run()) is None
    assert "illisible" in caplog.text


def test_deluge_publishes_private(fake_http, monkeypatch):
    monkeypatch.setitem(DELUGE_TORRENT, "private", True)
    fake_http["http://deluge"] = deluge_handler([])

    async def run():
        async with DelugeClient("http://deluge", "secret") as client:
            [torrent] = await client.get_torrents()
            return await client.private_flag(torrent)

    assert asyncio.run(run()) is True


def test_transmission_asks_for_is_private(fake_http, monkeypatch):
    monkeypatch.setitem(TRANSMISSION_TORRENT, "isPrivate", False)
    calls: list[dict] = []
    fake_http["http://transmission"] = transmission_handler(calls)

    async def run():
        async with TransmissionClient("http://transmission", None, None) as client:
            [torrent] = await client.get_torrents()
            return await client.private_flag(torrent)

    assert asyncio.run(run()) is False
    assert "isPrivate" in calls[-1]["arguments"]["fields"]


def test_the_scan_stores_the_flag(fake_http, settings):
    fake_http["http://qbit"] = qbit_server([{"hash": "a", "name": "A", "private": True}], {}, [])

    fetched = asyncio.run(fetch_torrents(settings))

    assert [row.is_private for row in fetched.rows] == [True]


# --- Favoris et activité ----------------------------------------------------------


def favorite_server(kind: str):
    """Faux serveur de test_media_server, avec favoris et activité."""
    base = servers.media_server(kind, [])

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        response = base(request)
        if response.status_code != 200:
            return response
        if request.url.path == "/Users":
            users = response.json()
            users[0]["LastActivityDate"] = "2026-09-20T18:30:00.0000000Z"
            return httpx.Response(200, json=users)
        if params.get("IncludeItemTypes") == "Movie":
            return httpx.Response(200, json={"Items": [{"Id": "m1", "UserData": {"IsFavorite": True}}]})
        if params.get("IncludeItemTypes") == "Series":
            return httpx.Response(
                200, json={"Items": [{"Id": "s1", "UserData": {"IsFavorite": True}}, {"Id": "s2", "UserData": {}}]}
            )
        return response

    return handler


@pytest.mark.parametrize("kind", ["emby", "jellyfin"])
def test_favorites_and_activity_come_without_extra_calls(fake_http, kind):
    fake_http[f"http://{kind}"] = favorite_server(kind)
    client = EmbyClient(f"http://{kind}", servers.KEY, kind)

    users = watch_stats.users_from_api(asyncio.run(client.get_users()))
    data = asyncio.run(watch_stats.collect_watch_data(client, users))

    [user] = users
    assert user.last_activity_at is not None and user.last_activity_at.isoformat().startswith("2026-09-20T18:30")
    movie = Media(id=1, media_type=MediaType.movie, title="Film", emby_item_id="m1")
    loved = Media(id=2, media_type=MediaType.series, title="Aimée", emby_item_id="s1", episode_count=2)
    other = Media(id=3, media_type=MediaType.series, title="Autre", emby_item_id="s2", episode_count=2)
    for media, favorite in ((movie, True), (loved, True), (other, False)):
        rows = watch_stats.build_watch_rows(media, users, data)
        watch_stats.apply_aggregates(media, rows, excluded=set())
        assert [r.favorite for r in rows] == [favorite]
        assert media.watch_favorite_count == int(favorite)


def test_an_excluded_user_does_not_count_as_a_favorite(fake_http):
    fake_http["http://emby"] = favorite_server("emby")
    client = EmbyClient("http://emby", servers.KEY, "emby")
    users = watch_stats.users_from_api(asyncio.run(client.get_users()))
    data = asyncio.run(watch_stats.collect_watch_data(client, users))
    movie = Media(id=1, media_type=MediaType.movie, title="Film", emby_item_id="m1")

    watch_stats.apply_aggregates(movie, watch_stats.build_watch_rows(movie, users, data), excluded={servers.USER_ID})

    assert movie.watch_favorite_count == 0


def test_the_users_route_shows_the_last_activity(admin_client, session, settings, fake_http):
    settings.emby_url = "http://emby"
    settings.emby_api_key = servers.KEY
    session.add(settings)
    session.commit()
    fake_http["http://emby"] = favorite_server("emby")

    [user] = admin_client.get("/api/emby/users").json()

    assert user["last_activity_at"].startswith("2026-09-20T18:30")


def test_the_detail_shows_favorites_privacy_and_series_status(admin_client, session, settings):
    series = Media(media_type=MediaType.series, title="Série", series_status="ended", watch_favorite_count=2)
    session.add(series)
    session.commit()
    session.add(Torrent(media_id=series.id, hash="h", name="Série.S01", is_private=True))
    session.add(EmbyUser(id="u1", name="Marie"))
    session.commit()

    detail = admin_client.get(f"/api/media/{series.id}").json()

    assert (detail["series_status"], detail["watch_favorite_count"]) == ("ended", 2)
    assert detail["torrents"][0]["is_private"] is True
    # Protection désactivée (installation existante) : aucune obligation.
    assert detail["torrents"][0]["seed_obligation"] is None
    [listed] = admin_client.get("/api/media").json()["items"]
    assert (listed["series_status"], listed["watch_favorite_count"]) == ("ended", 2)


# --- Statut des séries -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("continuing", "continuing"), ("ended", "ended"), ("upcoming", "upcoming"), ("deleted", "deleted"), ("tbd", None)],
)
def test_series_status_is_a_closed_list(raw, expected):
    assert series_status({"status": raw}) == expected
    assert series_status({}) is None


def test_the_full_scan_stores_the_series_status(fake_http, portable_inodes, session, settings, tmp_path):
    from app.services.scan import run_scan
    from tests import test_scan_characterization as world

    w = world.build_world(tmp_path)
    settings.emby_library_path = w["media"]
    settings.qbittorrent_download_path = w["torrents"]
    session.add(settings)
    session.commit()

    def sonarr(request: httpx.Request) -> httpx.Response:
        response = world.sonarr_handler(request)
        if request.url.path == "/api/v3/series":
            body = response.json()
            for entry in body:
                entry["status"] = "ended"
            return httpx.Response(200, json=body)
        return response

    fake_http["http://emby"] = world.emby_handler(w)
    fake_http["http://radarr"] = world.radarr_handler
    fake_http["http://sonarr"] = sonarr
    fake_http["http://qbit"] = world.qbit_handler(w)

    asyncio.run(run_scan())

    session.expire_all()
    medias = list(session.exec(select(Media)).all())
    assert {m.series_status for m in medias if m.media_type == MediaType.series} == {"ended"}
    assert {m.series_status for m in medias if m.media_type == MediaType.movie} == {None}
