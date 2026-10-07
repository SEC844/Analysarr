import asyncio

import httpx
import pytest

from app.clients.arr import NexcrateSonarrClient
from app.models.media import MediaType
from app.schemas.media import MediaDeleteSelection
from app.services import scan
from app.services.arr_instances import ReadOnlyArrError, arr_targets, is_nexcrate_url
from app.services.ignores import IgnoreSet
from app.services.media_delete import execute_media_delete
from tests.test_arr_instances import EMBY_MATRIX, HD, UHD, emby_handler, qbit_handler
from tests.test_media_delete import add_media, recorder

NEXCRATE_RADARR = "http://nexcrate/bazarr/radarr"
NEXCRATE_SONARR = "http://nexcrate/bazarr/sonarr"


@pytest.fixture
def nexcrate(session, settings):
    settings.radarr_url, settings.sonarr_url = NEXCRATE_RADARR, NEXCRATE_SONARR + "/"
    session.add(settings)
    session.commit()
    return settings


def test_nexcrate_is_recognised_by_its_fixed_address(session, settings):
    assert is_nexcrate_url("https://host/nexcrate/Bazarr/Radarr/")
    assert not is_nexcrate_url("http://radarr:7878") and not is_nexcrate_url(None)
    assert not arr_targets(session, settings, "radarr")[0].read_only


def test_two_versions_of_a_movie_are_not_duplicates_of_each_other(fake_http, session, nexcrate):
    def movie(movie_id, file_id, path, size):
        file = {"id": file_id, "path": path, "size": size}
        return {"id": movie_id, "title": "Matrix", "tmdbId": 603, "hasFile": True, "movieFile": file}

    movies = [movie(1, 11, HD, 100), movie(2, 21, UHD, 400)]

    def nexcrate_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/bazarr/radarr/api/v3/movie":
            return httpx.Response(200, json=movies)
        if req.url.path == "/bazarr/sonarr/api/v3/series":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"detail": "not_found"})  # file d'attente, historique

    fake_http["http://emby"] = emby_handler([EMBY_MATRIX])
    fake_http["http://nexcrate"] = nexcrate_handler
    fake_http["http://qbit"] = qbit_handler
    targets = arr_targets(session, nexcrate, "radarr"), arr_targets(session, nexcrate, "sonarr")
    results = asyncio.run(scan._collect(nexcrate, 0, *targets, IgnoreSet()))[0]

    by_id = {r.media.radarr_id: r for r in results}
    assert [(f.path, f.is_current) for f in by_id[1].files] == [(HD, True)]
    assert [(f.path, f.is_current) for f in by_id[2].files] == [(UHD, True)]
    assert all("doublon" not in r.media.statuses for r in results)


def test_series_get_the_file_count_nexcrate_does_not_send(fake_http):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/bazarr/sonarr/api/v3/series":
            return httpx.Response(200, json=[{"id": 7, "title": "Lost", "tvdbId": 73739}])
        assert (req.url.path, req.url.params["seriesId"]) == ("/bazarr/sonarr/api/v3/episodefile", "7")
        return httpx.Response(200, json=[{"id": 1}, {"id": 2}])

    fake_http["http://nexcrate"] = handler
    [series] = asyncio.run(NexcrateSonarrClient(NEXCRATE_SONARR, "key").get_series())
    assert series["statistics"] == {"episodeFileCount": 2}


def test_the_tracked_file_cannot_be_deleted_but_a_duplicate_can(fake_http, session, nexcrate, tmp_path):
    calls = []
    fake_http["http://nexcrate"] = recorder(calls)
    media, (tracked, duplicate) = add_media(session, tmp_path, MediaType.movie, files=2, radarr_id=1)
    tracked.is_current = True
    session.add(tracked)
    session.commit()

    for refused in (
        MediaDeleteSelection(media_file_ids=[tracked.id]),
        MediaDeleteSelection(media_file_ids=[duplicate.id], remove_from_arr=True),
    ):
        with pytest.raises(ReadOnlyArrError):
            asyncio.run(execute_media_delete(session, media, nexcrate, refused))
    assert calls == [] and (tmp_path / tracked.path).exists() and (tmp_path / duplicate.path).exists()

    selection = MediaDeleteSelection(media_file_ids=[duplicate.id])
    result = asyncio.run(execute_media_delete(session, media, nexcrate, selection))
    assert [s.success for s in result.steps] == [True]
    assert (tmp_path / tracked.path).exists() and not (tmp_path / duplicate.path).exists()
