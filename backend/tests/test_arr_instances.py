import asyncio

import httpx

from app.models.arr_instance import ArrInstance
from app.models.media import Media, MediaType
from app.schemas.media import MediaDeleteSelection
from app.services import scan
from app.services.arr_instances import arr_target_for, arr_targets
from app.services.media_delete import execute_media_delete
from tests.test_media_delete import add_media, recorder

RADARR_4K = {"kind": "radarr", "name": "Radarr 4K", "url": "http://radarr4k", "api_key": "radarr-4k-key"}
HD = "/data/media/movies/Matrix (1999)/Matrix.1080p.mkv"
UHD = "/data/media/movies/Matrix (1999)/Matrix.2160p.mkv"
EMBY_MATRIX = {
    "Id": "m1",
    "ProviderIds": {"Tmdb": "603"},
    "ImageTags": {},
    "MediaSources": [{"Path": HD, "Size": 100}, {"Path": UHD, "Size": 400}],
}


def put_instances(client, instances):
    return client.put("/api/settings", json={"arr_instances": instances})


def test_extra_instances_are_saved_with_write_only_keys(admin_client, settings, session):
    res = put_instances(admin_client, [RADARR_4K])
    assert res.status_code == 200 and "radarr-4k-key" not in res.text
    [saved] = res.json()["arr_instances"]
    assert (saved["kind"], saved["name"], saved["api_key_set"]) == ("radarr", "Radarr 4K", True)

    renamed = put_instances(admin_client, [{**RADARR_4K, "id": saved["id"], "name": "UHD", "api_key": ""}])
    assert renamed.json()["arr_instances"][0]["name"] == "UHD"
    assert session.get(ArrInstance, saved["id"]).api_key == "radarr-4k-key"

    # Liste absente : inchangée. Liste vide : instances supprimées.
    assert len(admin_client.put("/api/settings", json={}).json()["arr_instances"]) == 1
    assert put_instances(admin_client, []).json()["arr_instances"] == []


def test_invalid_extra_instances_are_rejected_without_partial_save(admin_client, settings):
    for instances in (
        [{**RADARR_4K, "api_key": ""}],
        [{**RADARR_4K, "url": "file:///etc/passwd"}],
        [{**RADARR_4K, "name": "   "}],
        [{**RADARR_4K, "id": 999}],
        [RADARR_4K] * 11,
        [RADARR_4K, {**RADARR_4K, "url": "ftp://radarr"}],
    ):
        assert put_instances(admin_client, instances).status_code == 400
    assert admin_client.get("/api/settings").json()["arr_instances"] == []


def test_deletion_goes_to_the_instance_tracking_the_media(fake_http, session, settings, tmp_path):
    primary_calls, uhd_calls = [], []
    fake_http["http://radarr"] = recorder(primary_calls)
    fake_http["http://radarr4k"] = recorder(uhd_calls)
    instance = ArrInstance(**RADARR_4K)
    session.add(instance)
    session.commit()
    media, files = add_media(session, tmp_path, MediaType.movie, radarr_id=42, arr_instance_id=instance.id)

    selection = MediaDeleteSelection(media_file_ids=[f.id for f in files], remove_from_arr=True)
    result = asyncio.run(execute_media_delete(session, media, settings, selection))

    assert primary_calls == []
    assert uhd_calls == [("DELETE", "/api/v3/movie/42", {"deleteFiles": "true", "addImportExclusion": "false"})]
    assert result.steps[0].label == "Titre retiré de Radarr 4K"


def test_a_deleted_instance_never_falls_back_to_the_primary(session, settings):
    assert arr_target_for(session, settings, Media(media_type=MediaType.movie, title="x", arr_instance_id=999)) is None
    assert arr_target_for(session, settings, Media(media_type=MediaType.series, title="x")).name == "Sonarr"


def emby_handler(movies):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/Items":
            return httpx.Response(200, json={"Items": movies if req.url.params.get("IncludeItemTypes") == "Movie" else []})
        return httpx.Response(200, json=[])  # /Users

    return handler


def radarr_handler(movie_file):
    movie = {"id": 1, "title": "Matrix", "year": 1999, "tmdbId": 603, "hasFile": True, "movieFile": movie_file}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[movie] if req.url.path == "/api/v3/movie" else [])

    return handler


def qbit_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/v2/auth/login":
        return httpx.Response(200, text="Ok.")
    return httpx.Response(200, json=[])


def collect(fake_http, session, settings, radarr_files):
    fake_http["http://emby"] = emby_handler([EMBY_MATRIX])
    fake_http["http://sonarr"] = lambda req: httpx.Response(200, json=[])
    fake_http["http://qbit"] = qbit_handler
    for host, movie_file in radarr_files.items():
        fake_http[host] = radarr_handler(movie_file)
    targets = arr_targets(session, settings, "radarr"), arr_targets(session, settings, "sonarr")
    return asyncio.run(scan._collect(settings, 0, *targets))[0]


def test_single_instance_still_flags_an_untracked_copy_as_duplicate(fake_http, session, settings):
    # Montages différents : Radarr voit /movies, le serveur multimédia /data/media/movies.
    [result] = collect(
        fake_http, session, settings, {"http://radarr": {"id": 11, "path": "/movies/Matrix (1999)/Matrix.1080p.mkv", "size": 100}}
    )
    assert [(f.path, f.is_current, f.arr_file_id) for f in result.files] == [(HD, True, 11), (UHD, False, None)]
    assert "doublon" in result.media.statuses


def test_versions_tracked_by_two_instances_are_not_duplicates(fake_http, session, settings):
    session.add(ArrInstance(**RADARR_4K))
    session.commit()
    results = collect(
        fake_http,
        session,
        settings,
        {
            "http://radarr": {"id": 11, "path": "/movies/Matrix (1999)/Matrix.1080p.mkv", "size": 100},
            "http://radarr4k": {"id": 21, "path": "/movies-4k/Matrix (1999)/Matrix.2160p.mkv", "size": 400},
        },
    )
    primary = next(r for r in results if r.media.arr_instance_id is None)
    uhd = next(r for r in results if r.media.arr_instance_id is not None)
    assert [(f.path, f.is_current) for f in primary.files] == [(HD, True)]
    assert [(f.path, f.is_current) for f in uhd.files] == [(UHD, True)]
    assert "doublon" not in primary.media.statuses and "doublon" not in uhd.media.statuses
