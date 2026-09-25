"""Ombi, alternative à Seer (issue #38) : faux serveur fidèle à son API v4
(code source : `RequestController`, `ApiKeyMiddlewear`, entités de demandes
sérialisées en camelCase par Newtonsoft)."""

import asyncio

import httpx

from app.clients.ombi import OmbiClient
from app.models.media import EmbyUser, Media, MediaType
from app.schemas.settings import ConnectionTestRequest
from app.services import seer
from app.services.connection_test import test_seer as run_connection_test

KEY = "ombi-key"
MARIE = {"userName": "marie", "alias": "Marie", "userType": 5, "providerUserId": "ABCD-1234"}
PLEX_USER = {"userName": "paul", "alias": "", "userType": 2, "providerUserId": "999"}
NEVER = "0001-01-01T00:00:00"

MOVIE = {
    "id": 7,
    "theMovieDbId": 603,
    "title": "Matrix",
    "requestedDate": "2026-03-12T10:00:00.1234567",
    "approved": True,
    "denied": None,
    "available": False,
    "requestedUser": MARIE,
    "has4KRequest": True,
    "requestedDate4k": "2026-03-14T09:00:00",
    "approved4K": False,
    "denied4K": True,
    "available4K": False,
}
ONLY_4K = {
    "id": 8,
    "theMovieDbId": 604,
    "requestedDate": NEVER,
    "approved": False,
    "has4KRequest": True,
    "requestedDate4k": "2026-03-15T09:00:00",
    "approved4K": True,
    "available4K": True,
    "requestedUser": PLEX_USER,
}
SERIES = {
    "id": 3,
    "tvDbId": 121361,
    "title": "Game of Thrones",
    "childRequests": [
        {
            "id": 30,
            "requestedDate": "2026-01-02T08:00:00",
            "approved": False,
            "available": False,
            "requestedUser": MARIE,
            "seasonRequests": [{"seasonNumber": 2}, {"seasonNumber": 1}],
        },
        {
            "id": 31,
            "requestedDate": "2026-02-02T08:00:00",
            "approved": True,
            "available": True,
            "requestedUser": None,
            "requestedByAlias": "API",
            "seasonRequests": [{"seasonNumber": 3}],
        },
    ],
}


def ombi_server(calls: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/api/v1/Status/info":  # [AllowAnonymous]
            return httpx.Response(200, json="4.47.1")
        if "apikey" not in request.headers:
            # Middleware sans clé : la requête suit son cours, et une route
            # inconnue tombe sur l'interface (UseSpa), en HTML.
            return httpx.Response(200, text="<!doctype html><html></html>")
        if request.headers["apikey"] != KEY:
            return httpx.Response(401, text="Invalid API Key")
        routes = {
            "/api/v1/Request/movie": [MOVIE, ONLY_4K],
            "/api/v1/Request/tv": [SERIES],
            "/api/v1/Request/movie/total": 2,
        }
        return httpx.Response(200, json=routes[path]) if path in routes else httpx.Response(404)

    return handler


def test_a_movie_request_carries_its_4k_request():
    normal, four_k = seer.parse_ombi_movie(MOVIE)

    assert normal[0] == ("movie", 603) and four_k[0] == ("movie", 603)
    assert (normal[1]["status"], normal[1]["is_4k"]) == ("approved", False)
    assert (four_k[1]["status"], four_k[1]["is_4k"]) == ("declined", True)
    assert normal[1]["requested_at"].isoformat() == "2026-03-12T10:00:00.123456+00:00"
    # Ombi ne dit ni qui a approuvé, ni si c'était automatique.
    assert normal[1]["modified_by_name"] is None and not normal[1]["auto_approved"]


def test_a_4k_only_request_has_no_normal_request():
    [(key, fields)] = seer.parse_ombi_movie(ONLY_4K)

    assert key == ("movie", 604) and fields["is_4k"] and fields["status"] == "completed"
    assert fields["requested_by_name"] == "paul"
    assert fields["requested_by_emby_id"] is None  # compte Plex : pas d'avatar du serveur multimédia


def test_series_requests_are_one_per_user_with_their_seasons():
    first, second = seer.parse_ombi_series(SERIES)

    assert first[0] == ("tv", 121361)
    assert (first[1]["seasons"], first[1]["status"]) == ("1,2", "pending")
    assert first[1]["requested_by_emby_id"] == "abcd1234"  # tirets retirés, minuscules
    assert (second[1]["seasons"], second[1]["status"], second[1]["requested_by_name"]) == ("3", "completed", "API")


def test_incomplete_requests_are_ignored():
    assert seer.parse_ombi_movie({"id": 1, "theMovieDbId": 0}) == []
    assert seer.parse_ombi_series({"id": 2, "tvDbId": 0, "childRequests": [{"id": 3}]}) == []
    assert seer.parse_ombi_series({"id": 2, "tvDbId": 5, "childRequests": [{"id": "x"}]}) == []


def test_the_scan_reads_ombi_when_it_is_the_chosen_manager(fake_http, session, settings):
    calls: list[httpx.Request] = []
    fake_http["http://ombi"] = ombi_server(calls)
    settings.seer_enabled, settings.seer_type = True, "ombi"
    settings.seer_url, settings.seer_api_key = "http://ombi", KEY
    session.add(EmbyUser(id="abcd1234", name="Marie", image_tag="t1"))
    movie = Media(media_type=MediaType.movie, title="Matrix", tmdb_id=603)
    series = Media(media_type=MediaType.series, title="GoT", tvdb_id=121361)
    session.add_all([movie, series])
    session.commit()

    client = seer.seer_client(settings)
    assert isinstance(client, OmbiClient)
    index = asyncio.run(seer.fetch_request_index(client))
    for media in (movie, series):
        session.add_all(seer.build_request_rows(media, index))
    session.commit()

    assert [(r.is_4k, r.status) for r in seer.build_requests_read(session, movie)] == [
        (False, "approved"),
        (True, "declined"),
    ]
    [marie, _api] = seer.build_requests_read(session, series)
    assert marie.requested_by.emby_user_id == "abcd1234" and marie.seasons == [1, 2]
    assert movie.requested_by == "Marie"
    assert {call.url.path for call in calls} == {"/api/v1/Request/movie", "/api/v1/Request/tv"}


def test_connection_test_validates_the_ombi_key(fake_http):
    fake_http["http://ombi"] = ombi_server([])

    ok = asyncio.run(run_connection_test(ConnectionTestRequest(url="http://ombi", api_key=KEY, request_manager="ombi")))
    refused = asyncio.run(
        run_connection_test(ConnectionTestRequest(url="http://ombi", api_key="faux", request_manager="ombi"))
    )

    assert ok.success and ok.message == "Connecté à Ombi (version 4.47.1)."
    assert not refused.success and "401" in refused.message


def test_connection_test_hints_at_a_wrong_manager_choice(fake_http):
    """Une instance Ombi testée comme Seer : sa route inconnue renvoie la page
    de l'interface (HTML, code 200), jamais un 404."""
    fake_http["http://ombi"] = ombi_server([])

    result = asyncio.run(run_connection_test(ConnectionTestRequest(url="http://ombi", api_key=KEY)))

    assert not result.success and "n'est peut-être pas Seer" in result.message


def test_the_chosen_manager_is_saved(admin_client):
    payload = {"seer_enabled": True, "seer_type": "ombi", "seer_url": "http://ombi", "seer_api_key": KEY}

    saved = admin_client.put("/api/settings", json=payload)

    assert saved.status_code == 200
    assert admin_client.get("/api/settings").json()["seer"]["kind"] == "ombi"
    assert admin_client.put("/api/settings", json=payload | {"seer_type": "overseerr"}).status_code == 422
