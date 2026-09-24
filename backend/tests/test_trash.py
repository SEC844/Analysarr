"""Corbeille : une suppression entière (fichiers, torrents, suivi Sonarr/Radarr,
demande Seer) est mise de côté et se restaure d'un bloc."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlmodel import select

from app.models.media import Media, MediaFile, MediaType, Torrent
from app.models.trash import TrashAction, TrashItem
from app.schemas.media import MediaDeleteSelection
from app.services.cascade_delete import execute_delete
from app.services.media_delete import execute_media_delete
from app.services.trash import TRASH_DIR_NAME, purge_expired, restore_action


def enable_trash(session, settings, tmp_path):
    settings.trash_enabled = True
    settings.emby_library_path = str(tmp_path)
    session.add(settings)
    session.commit()


def add_movie(session, tmp_path, name="Film.mkv", **fields):
    media = Media(media_type=MediaType.movie, title="Titre", **fields)
    session.add(media)
    session.commit()
    path = tmp_path / name
    path.write_bytes(b"contenu")
    row = MediaFile(media_id=media.id, path=str(path), size=7)
    session.add(row)
    session.commit()
    return media, row


def add_torrent(session, media, tmp_path, name="Film.2020.mkv"):
    path = tmp_path / name
    path.write_bytes(b"donnees")
    torrent = Torrent(
        media_id=media.id,
        hash="abc123",
        name=name,
        save_path=str(tmp_path),
        content_path=str(path),
        category="films",
        size=7,
        is_hardlinked=False,
        repairable=False,
    )
    session.add(torrent)
    session.commit()
    return torrent, path


class FakeTorrentClient:
    """Client torrent minimal : retient ce qui lui est demandé, pour vérifier
    qu'une suppression avec corbeille ne détruit JAMAIS les données."""

    def __init__(self):
        self.deleted: list[tuple[list[str], bool]] = []
        self.added: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def export_torrent(self, torrent_hash):
        return None  # comme Deluge/Transmission : repli sur le magnet

    async def get_trackers(self, torrent_hash):
        return [{"url": "https://tracker.example/announce?passkey=x", "status": None}]

    async def delete_torrents(self, hashes, delete_files):
        self.deleted.append((list(hashes), delete_files))

    async def add_torrent(self, *, torrent=None, magnet=None, save_path=None, category=None, paused=False):
        self.added.append({"magnet": magnet, "torrent": torrent, "save_path": save_path, "category": category})


def patch_client(monkeypatch, client):
    for module in ("deletion", "media_delete", "cascade_delete", "trash"):
        monkeypatch.setattr(f"app.services.{module}.torrent_client", lambda settings, c=client: c, raising=False)
    monkeypatch.setattr("app.clients.torrent.torrent_client", lambda settings, c=client: c)
    monkeypatch.setattr("app.clients.torrent.torrent_client_configured", lambda settings: True)


def test_deletion_removes_everything_when_the_trash_is_off(session, settings, tmp_path, monkeypatch):
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    media, row = add_movie(session, tmp_path)
    torrent, data = add_torrent(session, media, tmp_path)

    asyncio.run(
        execute_media_delete(
            session, media, settings, MediaDeleteSelection(media_file_ids=[row.id], torrent_ids=[torrent.id])
        )
    )

    # Corbeille désactivée : tout est d'abord mis de côté (pour pouvoir tout
    # annuler en cas d'échec), puis supprimé dès que la suppression a réussi.
    assert client.deleted == [(["abc123"], False)]
    assert not os.path.exists(row.path) and not os.path.exists(data)
    assert session.exec(select(TrashAction)).all() == []


def test_a_deletion_is_kept_as_one_restorable_action(session, settings, tmp_path, monkeypatch):
    enable_trash(session, settings, tmp_path)
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    media, row = add_movie(session, tmp_path)
    torrent, data = add_torrent(session, media, tmp_path)
    original_file, original_data = row.path, str(data)

    asyncio.run(
        execute_media_delete(
            session, media, settings, MediaDeleteSelection(media_file_ids=[row.id], torrent_ids=[torrent.id])
        )
    )

    # Le client n'a JAMAIS supprimé les données : c'est la corbeille qui les garde.
    assert client.deleted == [(["abc123"], False)]
    action = session.exec(select(TrashAction)).one()
    items = session.exec(select(TrashItem).where(TrashItem.action_id == action.id)).all()
    assert {item.kind for item in items} == {"library_file", "torrent"}
    assert not os.path.exists(original_file) and not os.path.exists(original_data)
    for item in items:
        payload = json.loads(item.torrent_payload) if item.torrent_payload else {}
        paths = payload.get("trashed_paths") or [item.trashed_path]
        assert all(os.path.exists(path) for path in paths)
        assert all(str(tmp_path / TRASH_DIR_NAME) == os.path.dirname(path) for path in paths)
    torrent_item = next(item for item in items if item.kind == "torrent")
    payload = json.loads(torrent_item.torrent_payload)
    assert payload["magnet"].startswith("magnet:?xt=urn:btih:abc123")
    assert "tracker.example" in payload["magnet"] and payload["save_path"] == str(tmp_path)


def test_restoring_puts_files_and_torrent_back(session, settings, tmp_path, monkeypatch):
    enable_trash(session, settings, tmp_path)
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    media, row = add_movie(session, tmp_path)
    torrent, data = add_torrent(session, media, tmp_path)
    original_file, original_data = row.path, str(data)
    asyncio.run(
        execute_media_delete(
            session, media, settings, MediaDeleteSelection(media_file_ids=[row.id], torrent_ids=[torrent.id])
        )
    )
    action = session.exec(select(TrashAction)).one()

    steps, complete = asyncio.run(restore_action(session, settings, action))

    assert complete and all(step.success for step in steps)
    assert Path(original_file).read_bytes() == b"contenu"
    assert Path(original_data).read_bytes() == b"donnees"
    assert client.added == [
        {
            "magnet": json.loads(json.dumps(client.added[0]["magnet"])),
            "torrent": None,
            "save_path": str(tmp_path),
            "category": "films",
        }
    ]
    session.expire_all()
    assert session.exec(select(TrashAction)).all() == []
    assert session.exec(select(TrashItem)).all() == []


def test_restoring_keeps_hardlinks(session, settings, tmp_path, monkeypatch):
    """Déplacement, jamais copie : le fichier revient avec le même inode, donc
    les hardlinks qui pointaient dessus restent valides."""
    enable_trash(session, settings, tmp_path)
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    media, row = add_movie(session, tmp_path)
    link = tmp_path / "hardlink.mkv"
    os.link(row.path, link)
    inode = os.stat(row.path).st_ino

    asyncio.run(execute_media_delete(session, media, settings, MediaDeleteSelection(media_file_ids=[row.id])))
    action = session.exec(select(TrashAction)).one()
    asyncio.run(restore_action(session, settings, action))

    assert os.stat(row.path).st_ino == inode == os.stat(link).st_ino


def test_a_failed_step_keeps_the_action_in_the_trash(session, settings, tmp_path, monkeypatch):
    enable_trash(session, settings, tmp_path)
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    media, row = add_movie(session, tmp_path)
    asyncio.run(execute_media_delete(session, media, settings, MediaDeleteSelection(media_file_ids=[row.id])))
    action = session.exec(select(TrashAction)).one()
    # Un fichier a repris la place : on ne l'écrase jamais.
    with open(row.path, "wb") as handle:
        handle.write(b"autre")

    steps, complete = asyncio.run(restore_action(session, settings, action))

    assert not complete and not steps[0].success
    session.expire_all()
    assert session.exec(select(TrashAction)).all() != []
    assert Path(row.path).read_bytes() == b"autre"


def test_cleanup_sends_orphan_torrents_to_the_trash(session, settings, tmp_path, monkeypatch):
    """Les automatisations passent par ce code : leurs suppressions doivent
    elles aussi être récupérables."""
    enable_trash(session, settings, tmp_path)
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    media = Media(media_type=MediaType.movie, title="Titre", statuses="orphelin_qbit")
    session.add(media)
    session.commit()
    torrent, data = add_torrent(session, media, tmp_path)

    asyncio.run(execute_delete(session, media, settings))

    assert client.deleted == [(["abc123"], False)]
    assert not os.path.exists(data)
    action = session.exec(select(TrashAction)).one()
    assert action.action == "cascade_delete"
    assert session.exec(select(TrashItem).where(TrashItem.action_id == action.id)).one().kind == "torrent"


def test_the_movie_is_restored_in_radarr(session, settings, tmp_path, fake_http, monkeypatch):
    enable_trash(session, settings, tmp_path)
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    calls = []

    def radarr(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"id": 42, "tmdbId": 7, "qualityProfileId": 1, "title": "Titre"})
        if request.method == "DELETE":
            return httpx.Response(200)
        return httpx.Response(201, json={"id": 77})

    fake_http["http://radarr"] = radarr
    media, row = add_movie(session, tmp_path, radarr_id=42)
    asyncio.run(
        execute_media_delete(
            session, media, settings, MediaDeleteSelection(media_file_ids=[row.id], remove_from_arr=True)
        )
    )
    action = session.exec(select(TrashAction)).one()
    assert json.loads(action.arr_payload)["body"]["tmdbId"] == 7

    steps, complete = asyncio.run(restore_action(session, settings, action))

    assert complete and any(step.kind == "arr_media" and step.success for step in steps)
    assert ("POST", "/api/v3/movie") in calls
    # Aucun rescan demandé : Radarr rafraîchit déjà la fiche qu'il ajoute, et un
    # second scan en parallèle enregistrait le fichier et ses NFO en double.
    assert ("POST", "/api/v3/command") not in calls


def test_retention_purges_whole_actions(session, settings, tmp_path):
    settings.trash_retention_days = 7
    kept, expired = tmp_path / "recent.mkv", tmp_path / "vieux.mkv"
    for path in (kept, expired):
        path.write_bytes(b"x")
    now = datetime.now(UTC).replace(tzinfo=None)
    for path, age in ((kept, 2), (expired, 9)):
        action = TrashAction(media_title="Titre", created_at=now - timedelta(days=age))
        session.add(action)
        session.commit()
        session.add(TrashItem(action_id=action.id, original_path="/a", trashed_path=str(path)))
        session.commit()

    assert purge_expired(session, settings) == 1
    assert os.path.exists(kept) and not os.path.exists(expired)
    assert len(session.exec(select(TrashAction)).all()) == 1


def test_trash_endpoints_group_by_action(admin_client, session, settings, tmp_path, monkeypatch):
    enable_trash(session, settings, tmp_path)
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    media, row = add_movie(session, tmp_path)
    torrent, _data = add_torrent(session, media, tmp_path)
    asyncio.run(
        execute_media_delete(
            session, media, settings, MediaDeleteSelection(media_file_ids=[row.id], torrent_ids=[torrent.id])
        )
    )

    listed = admin_client.get("/api/trash").json()
    assert len(listed) == 1
    assert listed[0]["media_title"] == "Titre" and listed[0]["restorable"]
    assert {item["kind"] for item in listed[0]["items"]} == {"library_file", "torrent"}

    restored = admin_client.post(f"/api/trash/{listed[0]['id']}/restore").json()
    assert restored["complete"] and restored["actions"] == []


def test_settings_are_bounded(admin_client, settings, session):
    from app.models.settings import Settings

    saved = admin_client.put("/api/trash/settings", json={"enabled": True, "retention_days": 30}).json()
    assert saved == {"enabled": True, "retention_days": 30, "min_days": 1, "max_days": 90}
    assert admin_client.put("/api/trash/settings", json={"enabled": True, "retention_days": 0}).status_code == 422
    assert admin_client.put("/api/trash/settings", json={"enabled": True, "retention_days": 400}).status_code == 422
    session.expire_all()
    assert session.get(Settings, 1).trash_retention_days == 30


def test_sonarr_never_deletes_the_files_when_the_trash_is_on(session, settings, tmp_path, fake_http, monkeypatch):
    """Bug réel : Sonarr supprimait la série avec ses fichiers, la corbeille ne
    récupérait que les rares fichiers qu'il n'avait pas su effacer — la
    restauration ne rendait que deux épisodes sur seize."""
    enable_trash(session, settings, tmp_path)
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    calls = []

    def sonarr(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.method == "GET":
            return httpx.Response(200, json={"id": 7, "tvdbId": 99, "title": "Titre"})
        return httpx.Response(200)

    fake_http["http://sonarr"] = sonarr
    media = Media(media_type=MediaType.series, title="Titre", sonarr_id=7)
    session.add(media)
    session.commit()
    files = []
    for index in range(3):
        path = tmp_path / f"S01E0{index + 1}.mkv"
        path.write_bytes(b"episode")
        row = MediaFile(media_id=media.id, path=str(path), size=7, episode_label=f"S01E0{index + 1}")
        session.add(row)
        files.append(row)
    session.commit()
    paths = [f.path for f in files]

    asyncio.run(
        execute_media_delete(
            session,
            media,
            settings,
            MediaDeleteSelection(media_file_ids=[f.id for f in files], remove_from_arr=True),
        )
    )

    delete_call = next(call for call in calls if call[0] == "DELETE")
    assert delete_call[2]["deleteFiles"] == "false"  # c'est Analysarr qui met de côté
    assert not any(os.path.exists(path) for path in paths)
    action = session.exec(select(TrashAction)).one()
    items = session.exec(select(TrashItem).where(TrashItem.action_id == action.id)).all()
    assert len(items) == 3 and all(os.path.exists(item.trashed_path) for item in items)

    steps, complete = asyncio.run(restore_action(session, settings, action))
    assert complete, [step.error for step in steps]
    assert all(os.path.exists(path) for path in paths)


def test_nothing_leaves_the_disk_if_sonarr_refuses(session, settings, tmp_path, fake_http, monkeypatch):
    """Promesse inchangée avec la corbeille : Sonarr refuse, les fichiers
    retournent à leur place."""
    enable_trash(session, settings, tmp_path)
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)

    def sonarr(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": 7, "tvdbId": 99})
        return httpx.Response(500)

    fake_http["http://sonarr"] = sonarr
    media = Media(media_type=MediaType.series, title="Titre", sonarr_id=7)
    session.add(media)
    session.commit()
    path = tmp_path / "S01E01.mkv"
    path.write_bytes(b"episode")
    row = MediaFile(media_id=media.id, path=str(path), size=7, episode_label="S01E01")
    session.add(row)
    session.commit()

    result = asyncio.run(
        execute_media_delete(
            session, media, settings, MediaDeleteSelection(media_file_ids=[row.id], remove_from_arr=True)
        )
    )

    assert any(not step.success for step in result.steps)
    assert os.path.exists(path)
    assert session.exec(select(TrashItem)).all() == []


def test_a_restore_is_recorded_and_triggers_a_scan(admin_client, session, settings, tmp_path, monkeypatch):
    from app.models.activity import ActionLog

    enable_trash(session, settings, tmp_path)
    client = FakeTorrentClient()
    patch_client(monkeypatch, client)
    scans = []
    monkeypatch.setattr("app.routers.trash.launch_scan", lambda scope="full": scans.append(scope) or True)
    media, row = add_movie(session, tmp_path)
    asyncio.run(execute_media_delete(session, media, settings, MediaDeleteSelection(media_file_ids=[row.id])))
    action_id = session.exec(select(TrashAction)).one().id

    result = admin_client.post(f"/api/trash/{action_id}/restore").json()

    assert result["complete"] and result["rescan_started"]
    assert scans == ["torrents"]  # aucun retrait Sonarr/Radarr à refaire
    session.expire_all()
    entry = session.exec(select(ActionLog)).one()
    assert entry.action == "trash_restore" and entry.media_title == "Titre"


def test_the_announced_size_counts_hardlinks_once(admin_client, session, settings, tmp_path, monkeypatch):
    """Bug réel : la corbeille annonçait la somme des tailles. Un film et le
    torrent qui le seede sont le MÊME fichier sur le disque (un seul inode,
    deux liens) : le purger ne libère cette taille qu'une fois."""
    enable_trash(session, settings, tmp_path)
    patch_client(monkeypatch, FakeTorrentClient())
    media = Media(media_type=MediaType.movie, title="Titre")
    session.add(media)
    session.commit()
    library = tmp_path / "Film.mkv"
    library.write_bytes(b"x" * 1000)
    download = tmp_path / "Film.2020.mkv"
    os.link(library, download)  # hardlink : même inode que la bibliothèque
    row = MediaFile(media_id=media.id, path=str(library), size=1000)
    torrent = Torrent(
        media_id=media.id,
        hash="abc123",
        name="Film.2020",
        save_path=str(tmp_path),
        content_path=str(download),
        size=1000,
        trackers_json="[]",
    )
    session.add(row)
    session.add(torrent)
    session.commit()

    asyncio.run(
        execute_media_delete(
            session, media, settings, MediaDeleteSelection(media_file_ids=[row.id], torrent_ids=[torrent.id])
        )
    )

    listed = admin_client.get("/api/trash").json()
    assert len(listed[0]["items"]) == 2
    assert listed[0]["size"] == 1000  # et non 2000


def test_a_file_still_linked_outside_the_trash_frees_nothing(
    admin_client, session, settings, tmp_path, monkeypatch
):
    """Le torrent reste en place et garde un lien vers le fichier : purger la
    corbeille ne rendra pas un octet tant que ce lien existe."""
    enable_trash(session, settings, tmp_path)
    patch_client(monkeypatch, FakeTorrentClient())
    media = Media(media_type=MediaType.movie, title="Titre")
    session.add(media)
    session.commit()
    library = tmp_path / "Film.mkv"
    library.write_bytes(b"x" * 1000)
    os.link(library, tmp_path / "Film.seed.mkv")  # le torrent reste en place
    row = MediaFile(media_id=media.id, path=str(library), size=1000)
    session.add(row)
    session.commit()

    asyncio.run(execute_media_delete(session, media, settings, MediaDeleteSelection(media_file_ids=[row.id])))

    listed = admin_client.get("/api/trash").json()
    assert listed[0]["items"][0]["size"] == 1000  # la taille du fichier reste affichée
    assert listed[0]["size"] == 0  # mais rien ne sera libéré
