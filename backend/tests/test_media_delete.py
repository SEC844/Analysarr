import asyncio
import os

import httpx
from sqlmodel import select

from app.models.media import Media, MediaFile, MediaRequest, MediaType
from app.schemas.media import MediaDeleteSelection
from app.services.media_delete import execute_media_delete


def recorder(calls, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(status)

    return handler


def add_media(session, tmp_path, media_type, files=1, **fields):
    media = Media(media_type=media_type, title="Titre", **fields)
    session.add(media)
    session.commit()
    rows = []
    for i in range(files):
        path = tmp_path / f"{media_type.value}-{media.id}-{i}.mkv"
        path.write_bytes(b"x")
        label = f"S01E0{i + 1}" if media_type == MediaType.series else None
        rows.append(MediaFile(media_id=media.id, path=str(path), size=1, episode_label=label))
    session.add_all(rows)
    session.commit()
    return media, rows


def test_whole_movie_is_removed_from_radarr_without_exclusion(fake_http, session, settings, tmp_path):
    calls = []
    fake_http["http://radarr"] = recorder(calls)
    media, files = add_media(session, tmp_path, MediaType.movie, radarr_id=42)

    selection = MediaDeleteSelection(media_file_ids=[f.id for f in files], remove_from_arr=True)
    result = asyncio.run(execute_media_delete(session, media, settings, selection))

    assert calls == [("DELETE", "/api/v3/movie/42", {"deleteFiles": "true", "addImportExclusion": "false"})]
    assert result.media_deleted and not os.path.exists(files[0].path)


def test_partial_series_selection_never_deletes_the_series(fake_http, session, settings, tmp_path):
    calls = []
    fake_http["http://sonarr"] = recorder(calls)
    media, files = add_media(session, tmp_path, MediaType.series, files=3, sonarr_id=7)

    selection = MediaDeleteSelection(media_file_ids=[f.id for f in files[:2]], remove_from_arr=True)
    result = asyncio.run(execute_media_delete(session, media, settings, selection))

    assert not any(path.startswith("/api/v3/series") for _, path, _ in calls)
    assert not result.media_deleted and os.path.exists(files[2].path)


def test_seer_request_is_kept_when_library_deletion_fails(fake_http, session, settings, tmp_path):
    radarr_calls, seer_calls = [], []
    fake_http["http://radarr"] = recorder(radarr_calls, status=500)
    fake_http["http://seer"] = recorder(seer_calls, status=204)
    settings.seer_enabled, settings.seer_url, settings.seer_api_key = True, "http://seer", "k"
    media, files = add_media(session, tmp_path, MediaType.movie, radarr_id=42)
    session.add(MediaRequest(media_id=media.id, seer_request_id=1, seer_media_id=100, status="approved"))
    session.commit()

    selection = MediaDeleteSelection(media_file_ids=[f.id for f in files], remove_from_arr=True, remove_from_seer=True)
    result = asyncio.run(execute_media_delete(session, media, settings, selection))

    assert seer_calls == []  # le média existe toujours : sa demande ne doit pas disparaître
    assert os.path.exists(files[0].path)
    assert any(not step.success for step in result.steps)


def test_seer_request_is_removed_after_successful_deletion(fake_http, session, settings, tmp_path):
    seer_calls = []
    fake_http["http://seer"] = recorder(seer_calls, status=204)
    settings.seer_enabled, settings.seer_url, settings.seer_api_key = True, "http://seer", "k"
    media, files = add_media(session, tmp_path, MediaType.movie)
    session.add(MediaRequest(media_id=media.id, seer_request_id=1, seer_media_id=100, status="approved"))
    session.commit()
    media_id = media.id

    selection = MediaDeleteSelection(media_file_ids=[f.id for f in files], remove_from_seer=True)
    asyncio.run(execute_media_delete(session, media, settings, selection))

    assert seer_calls == [("DELETE", "/api/v1/media/100", {})]
    assert session.exec(select(MediaRequest).where(MediaRequest.media_id == media_id)).all() == []


def test_selection_is_limited_to_the_media_files(fake_http, session, settings, tmp_path):
    media, _ = add_media(session, tmp_path, MediaType.movie)
    other, other_files = add_media(session, tmp_path, MediaType.movie)

    selection = MediaDeleteSelection(media_file_ids=[other_files[0].id])
    asyncio.run(execute_media_delete(session, media, settings, selection))

    assert os.path.exists(other_files[0].path)  # un id d'un autre média est ignoré


def test_a_file_already_gone_is_not_an_error(fake_http, session, settings, tmp_path):
    """Sonarr supprime ses fichiers en tâche de fond : le nôtre peut avoir
    disparu entre la vérification et la suppression. L'objectif est atteint."""
    fake_http["http://sonarr"] = recorder([], status=404)
    media, files = add_media(session, tmp_path, MediaType.series, files=2, sonarr_id=7)
    for f in files:
        f.arr_file_id = 900 + f.id
        session.add(f)
    session.commit()
    os.remove(files[0].path)

    selection = MediaDeleteSelection(media_file_ids=[f.id for f in files])
    result = asyncio.run(execute_media_delete(session, media, settings, selection))

    # 404 côté Sonarr = fichier déjà retiré de son côté : aucune erreur affichée.
    assert all(step.success for step in result.steps), [s.error for s in result.steps]
