"""Retrouver un film DÉJÀ suivi par Radarr, face au vrai comportement de son
API : `GET /api/v3/movie` ne filtre que par `tmdbId` (MovieController.AllMovie)
et renvoie TOUS les films pour un paramètre inconnu comme `imdbId`.

Bug réel : un film supprimé puis restauré depuis la corbeille n'était jamais
recréé dans Radarr — le premier film de la bibliothèque passait pour « déjà
suivi »."""

import asyncio
import json

import httpx

from app.clients.arr import RadarrClient
from app.models.trash import TrashAction
from app.services.trash import restore_action
from tests.test_trash import FakeTorrentClient, enable_trash, patch_client

OTHER_MOVIE = {"id": 5, "title": "Heat", "tmdbId": 949, "imdbId": "tt0113277"}
DELETED = {"id": 42, "title": "Bonnie et Clyde", "tmdbId": 475, "imdbId": "tt0061418"}


def real_radarr(library: list[dict], calls: list[tuple[str, str]]):
    """Radarr tel qu'il se comporte : filtre `tmdbId`, ignore tout le reste."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/api/v3/movie":
            tmdb_id = request.url.params.get("tmdbId")
            if tmdb_id is not None:
                return httpx.Response(200, json=[m for m in library if str(m["tmdbId"]) == tmdb_id])
            return httpx.Response(200, json=library)
        if request.method == "POST" and request.url.path == "/api/v3/movie":
            created = json.loads(request.content) | {"id": 99}
            library.append(created)
            return httpx.Response(201, json=created)
        return httpx.Response(404)

    return handler


def test_a_movie_missing_from_radarr_is_never_found_through_another_one(fake_http):
    fake_http["http://radarr"] = real_radarr([OTHER_MOVIE], [])
    radarr = RadarrClient("http://radarr", "k")

    assert asyncio.run(radarr.find_movie(475, "tt0061418")) is None
    assert asyncio.run(radarr.find_movie(None, "tt0061418")) is None
    assert asyncio.run(radarr.find_movie(None, "tt0113277")) == OTHER_MOVIE
    assert asyncio.run(radarr.find_movie(949, None)) == OTHER_MOVIE


def test_restoring_a_deleted_movie_adds_it_back_to_radarr(fake_http, session, settings, tmp_path, monkeypatch):
    enable_trash(session, settings, tmp_path)
    patch_client(monkeypatch, FakeTorrentClient())
    calls: list[tuple[str, str]] = []
    library = [OTHER_MOVIE]
    fake_http["http://radarr"] = real_radarr(library, calls)
    action = TrashAction(
        action="delete_selection",
        media_title="Bonnie et Clyde",
        media_type="movie",
        arr_payload=json.dumps({"service": "radarr", "instance_id": None, "body": DELETED}),
    )
    session.add(action)
    session.commit()

    steps, complete = asyncio.run(restore_action(session, settings, action))

    assert complete, [step.error for step in steps]
    assert ("POST", "/api/v3/movie") in calls
    assert [m["title"] for m in library] == ["Heat", "Bonnie et Clyde"]
