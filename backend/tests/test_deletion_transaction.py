"""Suppression en tout ou rien (services/deletion.py).

Bug réel à l'origine : le torrent était retiré du client, puis la suppression
échouait sur le fichier de la bibliothèque (droits). Le média n'était plus
seedé, restait dans la bibliothèque, et la corbeille ne pouvait rien rendre.
Désormais : tout est vérifié avant, et au moindre échec tout est remis en
place."""

import asyncio
import json
import os

import httpx
import pytest
from sqlmodel import select

from app.models.media import MediaFile, MediaType, Torrent
from app.models.trash import TrashAction, TrashItem
from app.schemas.media import MediaDeleteSelection
from app.services import trash as trash_service
from app.services.cascade_delete import execute_delete
from app.services.media_delete import execute_media_delete
from app.services.path_guard import MountUnavailableError, PathNotWritableError
from app.services.trash import item_available, restore_action
from tests.test_trash import FakeTorrentClient, add_movie, add_torrent, enable_trash, patch_client


class FailingTorrentClient(FakeTorrentClient):
    """Le client refuse de retirer le torrent (client en erreur, torrent
    verrouillé...)."""

    async def delete_torrents(self, hashes, delete_files):
        raise RuntimeError("qBittorrent a refusé la suppression")


def radarr(calls, *, delete_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"id": 42, "title": "Titre", "tmdbId": 603})
        if request.method == "DELETE":
            return httpx.Response(delete_status)
        return httpx.Response(201, json={"id": 43})

    return handler


def whole_movie(row, torrent):
    return MediaDeleteSelection(media_file_ids=[row.id], torrent_ids=[torrent.id], remove_from_arr=True)


@pytest.mark.parametrize("trash_enabled", [False, True])
def test_a_radarr_refusal_touches_nothing(fake_http, session, settings, tmp_path, monkeypatch, trash_enabled):
    if trash_enabled:
        enable_trash(session, settings, tmp_path)
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    calls = []
    fake_http["http://radarr"] = radarr(calls, delete_status=500)
    media, row = add_movie(session, tmp_path, radarr_id=42)
    torrent, data = add_torrent(session, media, tmp_path)
    path = row.path

    result = asyncio.run(execute_media_delete(session, media, settings, whole_movie(row, torrent)))

    assert os.path.exists(path) and os.path.exists(data)  # fichier remis en place
    assert client.deleted == []  # torrent jamais retiré du client : il seede toujours
    assert [(step.kind, step.success) for step in result.steps] == [("arr_media", False), ("rollback", False)]
    assert session.exec(select(TrashAction)).all() == []  # rien ne reste en corbeille
    assert session.exec(select(MediaFile)).all() and session.exec(select(Torrent)).all()


def test_a_torrent_client_refusal_puts_everything_back(fake_http, session, settings, tmp_path, monkeypatch):
    """Le client refuse en dernier : le fichier revient, et le film retiré de
    Radarr y est recréé à partir de sa fiche mise de côté."""
    patch_client(monkeypatch, FailingTorrentClient())
    calls = []
    fake_http["http://radarr"] = radarr(calls)
    media, row = add_movie(session, tmp_path, radarr_id=42)
    torrent, data = add_torrent(session, media, tmp_path)
    path = row.path

    result = asyncio.run(execute_media_delete(session, media, settings, whole_movie(row, torrent)))

    assert os.path.exists(path) and os.path.exists(data)
    assert ("DELETE", "/api/v3/movie/42") in calls and ("POST", "/api/v3/movie") in calls
    assert [(step.kind, step.success) for step in result.steps] == [("torrent", False), ("rollback", False)]
    assert not result.media_deleted and session.exec(select(TrashAction)).all() == []


def test_missing_write_permission_is_refused_before_anything(fake_http, session, settings, tmp_path, monkeypatch):
    """Cas réel : l'utilisateur du conteneur ne peut pas écrire dans le
    dossier du film. Rien n'est tenté, et le message dit quoi corriger."""
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    calls = []
    fake_http["http://radarr"] = radarr(calls)
    media, row = add_movie(session, tmp_path, radarr_id=42)
    torrent, _data = add_torrent(session, media, tmp_path)
    library_dir = os.path.dirname(row.path)
    monkeypatch.setattr(trash_service, "writable_dir", lambda directory: directory != library_dir)

    with pytest.raises(PathNotWritableError) as refused:
        asyncio.run(execute_media_delete(session, media, settings, whole_movie(row, torrent)))

    assert library_dir in str(refused.value) and "PUID/PGID" in str(refused.value)
    assert os.path.exists(row.path) and client.deleted == [] and calls == []
    assert session.exec(select(TrashAction)).all() == []


def test_the_api_answers_409_with_the_reason(admin_client, fake_http, session, settings, tmp_path, monkeypatch):
    patch_client(monkeypatch, FakeTorrentClient())
    media, row = add_movie(session, tmp_path, radarr_id=42)
    library_dir = os.path.dirname(row.path)
    monkeypatch.setattr(trash_service, "writable_dir", lambda directory: directory != library_dir)

    response = admin_client.post(f"/api/media/{media.id}/delete-selection", json={"media_file_ids": [row.id]})

    assert response.status_code == 409
    assert "Aucune modification" in response.json()["detail"]


def test_torrent_data_out_of_reach(session, settings, tmp_path, monkeypatch):
    """Données invisibles depuis le conteneur : impossibles à garder en
    corbeille (refus), et supprimées par le client en dernier sinon."""
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    media, _row = add_movie(session, tmp_path)
    torrent, data = add_torrent(session, media, tmp_path)
    os.remove(data)
    selection = MediaDeleteSelection(torrent_ids=[torrent.id])

    asyncio.run(execute_media_delete(session, media, settings, selection))
    assert client.deleted == [(["abc123"], True)]

    media, _row = add_movie(session, tmp_path, name="Autre.mkv")
    torrent, data = add_torrent(session, media, tmp_path, name="Autre.2020.mkv")
    os.remove(data)
    enable_trash(session, settings, tmp_path)
    with pytest.raises(MountUnavailableError):
        asyncio.run(execute_media_delete(session, media, settings, MediaDeleteSelection(torrent_ids=[torrent.id])))


def test_an_interrupted_episode_deletion_is_monitored_again(fake_http, session, settings, tmp_path, monkeypatch):
    """Épisode oublié par Sonarr et démonitoré, puis échec du client torrent :
    Sonarr relit la série (le fichier est revenu) et l'épisode redevient
    surveillé."""
    patch_client(monkeypatch, FailingTorrentClient())
    calls = []

    def sonarr(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        return httpx.Response(200, json={})

    fake_http["http://sonarr"] = sonarr
    media, first = add_movie(session, tmp_path, name="S01E01.mkv", sonarr_id=7)
    media.media_type = MediaType.series
    first.arr_file_id, first.sonarr_episode_id, first.episode_label = 900, 77, "S01E01"
    second_path = tmp_path / "S01E02.mkv"
    second_path.write_bytes(b"x")
    session.add_all([media, first, MediaFile(media_id=media.id, path=str(second_path), size=1, episode_label="S01E02")])
    session.commit()
    torrent, _data = add_torrent(session, media, tmp_path)
    # Un épisode sur deux : la série reste dans Sonarr, l'épisode est démonitoré.
    selection = MediaDeleteSelection(media_file_ids=[first.id], torrent_ids=[torrent.id], remove_from_arr=True)

    asyncio.run(execute_media_delete(session, media, settings, selection))

    assert os.path.exists(first.path)
    assert ("DELETE", "/api/v3/episodefile/900", None) in calls
    assert ("PUT", "/api/v3/episode/monitor", {"episodeIds": [77], "monitored": False}) in calls
    assert ("PUT", "/api/v3/episode/monitor", {"episodeIds": [77], "monitored": True}) in calls
    assert ("POST", "/api/v3/command", {"name": "RescanSeries", "seriesId": 7}) in calls


def test_a_cleanup_is_all_or_nothing(session, settings, tmp_path, monkeypatch):
    patch_client(monkeypatch, FailingTorrentClient())
    media, row = add_movie(session, tmp_path)
    duplicate = tmp_path / "Film.old.mkv"
    duplicate.write_bytes(b"ancien")
    session.add(MediaFile(media_id=media.id, path=str(duplicate), size=6, is_current=False))
    row.is_current = True
    session.add(row)
    session.commit()
    add_torrent(session, media, tmp_path)

    result = asyncio.run(execute_delete(session, media, settings))

    assert os.path.exists(duplicate)  # le doublon est revenu : le torrent n'a pas pu partir
    assert [(step.kind, step.success) for step in result.steps] == [("torrent", False), ("rollback", False)]
    assert session.exec(select(TrashAction)).all() == []


def test_a_torrent_whose_data_never_moved_can_be_restored(session, settings, tmp_path, monkeypatch):
    """Élément laissé par l'ancien déroulé (torrent retiré du client, données
    restées en place) : restaurable, il suffit de remettre le torrent."""
    enable_trash(session, settings, tmp_path)
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    action = TrashAction(action="delete_selection", media_title="Bonnie et Clyde", media_type="movie")
    session.add(action)
    session.commit()
    payload = {"hash": "abc123", "name": "Bonnie", "save_path": str(tmp_path), "magnet": "magnet:?xt=urn:btih:abc123"}
    item = TrashItem(action_id=action.id, kind="torrent", label="Bonnie", size=1, torrent_payload=json.dumps(payload))
    session.add(item)
    session.commit()

    assert item_available(item)
    steps, complete = asyncio.run(restore_action(session, settings, action))

    assert complete, [step.error for step in steps]
    assert client.added and client.added[0]["save_path"] == str(tmp_path)


def test_restoring_never_duplicates_a_movie_radarr_still_tracks(fake_http, session, settings, tmp_path, monkeypatch):
    """Suppression interrompue avant le retrait de Radarr : la fiche mise de
    côté ne doit pas être recréée en double (Radarr refuserait l'ajout)."""
    enable_trash(session, settings, tmp_path)
    patch_client(monkeypatch, FakeTorrentClient())
    calls = []

    def radarr_tracking(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json=[{"id": 42, "tmdbId": 603}])

    fake_http["http://radarr"] = radarr_tracking
    body = {"id": 42, "title": "Bonnie et Clyde", "tmdbId": 603}
    action = TrashAction(
        action="delete_selection",
        media_title="Bonnie et Clyde",
        media_type="movie",
        arr_payload=json.dumps({"service": "radarr", "instance_id": None, "body": body}),
    )
    session.add(action)
    session.commit()

    steps, complete = asyncio.run(restore_action(session, settings, action))

    assert complete, [step.error for step in steps]
    assert ("POST", "/api/v3/movie") not in calls
