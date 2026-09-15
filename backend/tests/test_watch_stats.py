import asyncio
import json

import httpx

from app.clients.emby import EmbyClient
from app.models.media import Media, MediaType
from app.routers.media import _matches_watch_filter
from app.services import watch_stats as ws

USERS = [
    {"Id": "u1", "Name": "Marie", "Policy": {"IsDisabled": False}},
    {"Id": "u2", "Name": "Paul", "Policy": {}},
    {"Id": "u3", "Name": "Ancien", "Policy": {"IsDisabled": True}},
    {"Id": "u4", "Name": "SansAcces", "Policy": {}},
]
MOVIES = {
    "u1": [{"Id": "m1", "UserData": {"Played": True, "LastPlayedDate": "2026-09-01T20:00:00.1234567Z"}}],
    "u2": [{"Id": "m1", "UserData": {"Played": False, "PlayedPercentage": 45.2, "LastPlayedDate": "2026-09-10T20:00:00Z"}}],
    "u3": [{"Id": "m1", "UserData": {"Played": True}}],
    "u4": [],
}
SERIES = {"u1": [{"Id": "s1"}], "u2": [{"Id": "s1"}], "u3": [{"Id": "s1"}], "u4": []}
PLAYED = {"u1": [{"SeriesId": "s1", "UserData": {}}] * 3, "u2": [{"SeriesId": "s1", "UserData": {}}], "u3": [], "u4": []}
RESUMING = {"u1": [], "u2": [{"SeriesId": "s1", "UserData": {"LastPlayedDate": "2026-09-12T10:00:00Z"}}], "u3": [], "u4": []}


def emby(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/Users":
        return httpx.Response(200, json=USERS)
    user = request.url.path.split("/")[2]
    kind, params = request.url.params.get("IncludeItemTypes"), request.url.params
    if kind == "Movie":
        items = [i for i in MOVIES[user] if not params.get("Ids") or i["Id"] == params["Ids"]]
    elif kind == "Series":
        items = SERIES[user]
    else:
        items = (PLAYED if params.get("Filters") == "IsPlayed" else RESUMING)[user]
    return httpx.Response(200, json={"Items": items})


def collect(fake_http):
    fake_http["http://emby"] = emby
    client = EmbyClient("http://emby", "k")
    users = ws.users_from_api(asyncio.run(client.get_users()))
    return users, asyncio.run(ws.collect_watch_data(client, users))


def test_only_active_users_with_access_are_counted(fake_http):
    users, data = collect(fake_http)
    movie = Media(id=1, media_type=MediaType.movie, title="Avatar", emby_item_id="m1")
    rows = ws.build_watch_rows(movie, users, data)
    ws.apply_aggregates(movie, rows, excluded=set())

    assert sorted(r.emby_user_id for r in rows) == ["u1", "u2"]  # u3 désactivé, u4 sans accès
    assert (movie.watch_played_count, movie.watch_user_count, movie.watch_in_progress_count) == (1, 2, 1)
    assert "u3" not in data  # jamais interrogé


def test_series_progress_counts_watched_episodes(fake_http):
    users, data = collect(fake_http)
    series = Media(id=2, media_type=MediaType.series, title="Série", emby_item_id="s1", episode_count=3)
    rows = {r.emby_user_id: r for r in ws.build_watch_rows(series, users, data)}

    assert (rows["u1"].progress, rows["u1"].played) == (3, True)
    assert (rows["u2"].progress, rows["u2"].played, rows["u2"].in_progress) == (1, False, True)


def test_excluded_users_leave_the_quota(fake_http):
    users, data = collect(fake_http)
    movie = Media(id=1, media_type=MediaType.movie, title="Avatar", emby_item_id="m1")
    rows = ws.build_watch_rows(movie, users, data)
    ws.apply_aggregates(movie, rows, excluded={"u1"})
    assert (movie.watch_played_count, movie.watch_user_count) == (0, 1)


def test_library_watch_filters():
    never = Media(media_type=MediaType.movie, title="a", watch_user_count=3)
    started = Media(media_type=MediaType.movie, title="b", watch_user_count=3, watch_in_progress_count=1)
    everyone = Media(media_type=MediaType.movie, title="c", watch_user_count=2, watch_played_count=2)
    assert _matches_watch_filter(never, "never") and not _matches_watch_filter(started, "never")
    assert _matches_watch_filter(started, "in_progress")
    assert _matches_watch_filter(everyone, "all") and not _matches_watch_filter(started, "all")


def test_excluded_ids_are_read_defensively():
    class Row:
        excluded_emby_user_ids = json.dumps(["u1", 42])

    assert ws.excluded_user_ids(Row()) == {"u1"}
    Row.excluded_emby_user_ids = "not json"
    assert ws.excluded_user_ids(Row()) == set()
