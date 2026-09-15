"""Emby et Jellyfin : faux serveurs fidèles aux différences d'API réelles
(spécification OpenAPI Jellyfin 10.11) — Jellyfin refuse X-Emby-Token par
défaut et n'expose plus /Users/{id}/Items."""

import asyncio

import httpx
import pytest

from app.clients.emby import EmbyClient
from app.schemas.settings import ConnectionTestRequest
from app.services import watch_stats
from app.services.connection_test import test_emby as run_connection_test

KEY = "server-key"
USER_ID = "0f8fad5bd9cb469fa165708167b3e8a1"
MOVIE = {"Id": "m1", "UserData": {"Played": False, "PlayedPercentage": 37.5}}


def media_server(kind: str, calls: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path, params = request.url.path, request.url.params
        if path == "/System/Info/Public":
            return httpx.Response(200, json={"ProductName": "Jellyfin Server" if kind == "jellyfin" else "Emby Server"})
        authorized = (
            request.headers.get("authorization") == f'MediaBrowser Token="{KEY}"'
            if kind == "jellyfin"
            else request.headers.get("x-emby-token") == KEY
        )
        if not authorized:
            return httpx.Response(401)
        if path == "/System/Info":
            return httpx.Response(200, json={"ServerName": "home", "Version": "10.11.0"})
        if path == "/Users":
            return httpx.Response(200, json=[{"Id": USER_ID, "Name": "Marie", "PrimaryImageTag": "t", "Policy": {}}])
        user_items = (kind == "jellyfin" and path == "/Items" and params.get("userId") == USER_ID) or (
            kind == "emby" and path == f"/Users/{USER_ID}/Items"
        )
        if user_items:
            return httpx.Response(200, json={"Items": [MOVIE] if params.get("IncludeItemTypes") == "Movie" else []})
        avatar = (kind == "jellyfin" and path == "/UserImage" and params.get("userId") == USER_ID) or (
            kind == "emby" and path == f"/Users/{USER_ID}/Images/Primary"
        )
        if avatar:
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        return httpx.Response(404)

    return handler


@pytest.mark.parametrize("kind", ["emby", "jellyfin"])
def test_watch_data_and_avatar_use_the_right_api(fake_http, kind):
    calls: list[httpx.Request] = []
    fake_http[f"http://{kind}"] = media_server(kind, calls)
    client = EmbyClient(f"http://{kind}", KEY, kind)

    users = watch_stats.users_from_api(asyncio.run(client.get_users()))
    data = asyncio.run(watch_stats.collect_watch_data(client, users))

    assert data[USER_ID].movies["m1"]["PlayedPercentage"] == 37.5
    assert asyncio.run(client.fetch_user_avatar(USER_ID)) is not None
    assert all(r.url.path != f"/Users/{USER_ID}/Items" for r in calls) if kind == "jellyfin" else True


def test_jellyfin_never_sends_legacy_token_header(fake_http):
    calls: list[httpx.Request] = []
    fake_http["http://jellyfin"] = media_server("jellyfin", calls)
    asyncio.run(EmbyClient("http://jellyfin", KEY, "jellyfin").get_users())
    assert calls and all("x-emby-token" not in r.headers for r in calls)


@pytest.mark.parametrize(
    ("kind", "chosen", "expected"),
    [
        ("jellyfin", "jellyfin", "Connecté à home (Jellyfin 10.11.0)."),
        ("emby", "emby", "Connecté à home (Emby 10.11.0)."),
        ("jellyfin", "emby", "est Jellyfin, pas Emby"),
        ("emby", "jellyfin", "est Emby, pas Jellyfin"),
    ],
)
def test_connection_test_detects_wrong_server_choice(fake_http, kind, chosen, expected):
    fake_http[f"http://{kind}"] = media_server(kind, [])
    result = asyncio.run(run_connection_test(ConnectionTestRequest(url=f"http://{kind}", api_key=KEY, media_server=chosen)))
    assert expected in result.message
    assert result.success == (kind == chosen)


def test_connection_test_reports_rejected_key(fake_http):
    fake_http["http://jellyfin"] = media_server("jellyfin", [])
    result = asyncio.run(
        run_connection_test(ConnectionTestRequest(url="http://jellyfin", api_key="wrong", media_server="jellyfin"))
    )
    assert not result.success
    assert "401" in result.message
