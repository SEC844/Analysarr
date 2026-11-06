"""Recherche cross-seed ciblée : ce qui est réellement envoyé au daemon.

Vérifié dans le code de cross-seed (v6.13.9 et v7, `preFilter.ts::filterByContent`) :
sans `includeSingleEpisodes=true`, un torrent ou un fichier d'un seul épisode
est rejeté, comme un fichier rangé dans un dossier « Season XX », et un
dossier nommé comme ceux de Sonarr est écarté. Sa route répond 204 avant même
de chercher : ces refus étaient silencieux."""

import asyncio
import os
from urllib.parse import parse_qs

import httpx

from app.models.media import MediaFile
from app.services.cross_seed import trigger_cross_seed_search

SERIES = "/media/tv/All's Fair (2025)"


def _settings(settings):
    settings.cross_seed_enabled = True
    settings.cross_seed_url = "http://cross-seed"
    settings.cross_seed_api_key = "cle-secrete"
    return settings


def _daemon(fake_http) -> list[dict[str, str]]:
    sent: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/webhook"
        sent.append({key: values[0] for key, values in parse_qs(request.content.decode()).items()})
        return httpx.Response(204)

    fake_http["http://cross-seed"] = handler
    return sent


def _paths(sent: list[dict[str, str]]) -> list[str]:
    # Séparateurs du système (os.path.commonpath) : comparés normalisés.
    return [os.path.normpath(s["path"]) for s in sent]


def _episodes() -> list[MediaFile]:
    return [
        MediaFile(media_id=1, path=f"{SERIES}/Season 01/All's Fair - S01E01.mkv", episode_label="S01E01"),
        MediaFile(media_id=1, path=f"{SERIES}/Season 01/All's Fair - S01E02.mkv", episode_label="S01E02"),
        MediaFile(media_id=1, path=f"{SERIES}/Season 02/All's Fair - S02E01.mkv", episode_label="S02E01"),
    ]


def test_an_episode_search_asks_for_single_episodes(fake_http, settings):
    sent = _daemon(fake_http)

    result = asyncio.run(trigger_cross_seed_search(_settings(settings), ["abc"], _episodes(), scope="episode"))

    assert result.triggered == 1 and result.errors == []
    assert sent == [{"infoHash": "abc", "ignoreExcludeRecentSearch": "true", "includeSingleEpisodes": "true"}]


def test_an_episode_search_without_torrent_sends_each_file(fake_http, settings):
    sent = _daemon(fake_http)

    asyncio.run(trigger_cross_seed_search(_settings(settings), [], _episodes(), scope="episode"))

    assert [s["path"] for s in sent] == [f.path for f in _episodes()]
    assert all(s["includeSingleEpisodes"] == "true" for s in sent)


def test_a_whole_series_search_goes_season_by_season(fake_http, settings):
    """Le dossier de la série (nommé comme ceux de Sonarr) serait écarté par
    cross-seed : jamais envoyé, une recherche par saison à la place."""
    sent = _daemon(fake_http)

    for scope in ("season", "series"):
        sent.clear()
        asyncio.run(trigger_cross_seed_search(_settings(settings), ["abc"], _episodes(), scope=scope))
        assert _paths(sent) == _paths([{"path": f"{SERIES}/Season 01"}, {"path": f"{SERIES}/Season 02"}])
        assert all(s["includeSingleEpisodes"] == "true" for s in sent)


def test_episodes_without_season_folders_are_searched_once(fake_http, settings):
    sent = _daemon(fake_http)
    flat = [
        MediaFile(media_id=1, path="/media/tv/Show/Show - S01E01.mkv", episode_label="S01E01"),
        MediaFile(media_id=1, path="/media/tv/Show/Show - S02E01.mkv", episode_label="S02E01"),
    ]

    asyncio.run(trigger_cross_seed_search(_settings(settings), [], flat, scope="season"))

    assert _paths(sent) == _paths([{"path": "/media/tv/Show"}])
