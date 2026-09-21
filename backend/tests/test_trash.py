"""Corbeille : une suppression devient un déplacement annulable pendant la
durée de rétention."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models.media import Media, MediaFile, MediaType
from app.models.trash import TrashEntry
from app.schemas.media import MediaDeleteSelection
from app.services.media_delete import execute_media_delete
from app.services.trash import TRASH_DIR_NAME, purge_expired


def add_movie(session, tmp_path, name="Film.mkv"):
    media = Media(media_type=MediaType.movie, title="Titre")
    session.add(media)
    session.commit()
    path = tmp_path / name
    path.write_bytes(b"contenu")
    row = MediaFile(media_id=media.id, path=str(path), size=7)
    session.add(row)
    session.commit()
    return media, row


def test_deletion_removes_the_file_when_the_trash_is_off(session, settings, tmp_path):
    media, row = add_movie(session, tmp_path)
    selection = MediaDeleteSelection(media_file_ids=[row.id])

    asyncio.run(execute_media_delete(session, media, settings, selection))

    assert not os.path.exists(row.path)
    assert session.exec(select(TrashEntry)).all() == []


def test_a_deleted_file_is_recoverable_while_the_trash_is_on(session, settings, tmp_path):
    settings.trash_enabled = True
    settings.emby_library_path = str(tmp_path)
    session.add(settings)
    session.commit()
    media, row = add_movie(session, tmp_path)
    original = row.path
    selection = MediaDeleteSelection(media_file_ids=[row.id])

    asyncio.run(execute_media_delete(session, media, settings, selection))

    assert not os.path.exists(original)
    entry = session.exec(select(TrashEntry)).one()
    # Déplacement (même inode), jamais une copie : le contenu est intact.
    assert os.path.dirname(entry.trashed_path) == str(tmp_path / TRASH_DIR_NAME)
    assert open(entry.trashed_path, "rb").read() == b"contenu"
    assert entry.original_path == original and entry.size == 7


def test_restoring_puts_the_file_back_and_never_overwrites(admin_client, session, settings, tmp_path):
    settings.trash_enabled = True
    settings.emby_library_path = str(tmp_path)
    session.add(settings)
    session.commit()
    media, row = add_movie(session, tmp_path)
    original = row.path
    asyncio.run(execute_media_delete(session, media, settings, MediaDeleteSelection(media_file_ids=[row.id])))
    entry = session.exec(select(TrashEntry)).one()

    listed = admin_client.get("/api/trash").json()
    assert listed[0]["original_path"] == original and listed[0]["available"]

    # Un fichier a repris la place : on ne l'écrase jamais.
    with open(original, "wb") as handle:
        handle.write(b"autre")
    assert admin_client.post(f"/api/trash/{entry.id}/restore").status_code == 409

    os.remove(original)
    assert admin_client.post(f"/api/trash/{entry.id}/restore").json() == []
    assert open(original, "rb").read() == b"contenu"
    session.expire_all()
    assert session.exec(select(TrashEntry)).all() == []


def test_retention_purges_old_entries_only(session, settings, tmp_path):
    settings.trash_retention_days = 7
    kept, expired = tmp_path / "recent.mkv", tmp_path / "vieux.mkv"
    for path in (kept, expired):
        path.write_bytes(b"x")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(TrashEntry(original_path="/a", trashed_path=str(kept), deleted_at=now - timedelta(days=2)))
    session.add(TrashEntry(original_path="/b", trashed_path=str(expired), deleted_at=now - timedelta(days=9)))
    session.commit()

    assert purge_expired(session, settings) == 1
    assert os.path.exists(kept) and not os.path.exists(expired)
    assert len(session.exec(select(TrashEntry)).all()) == 1


def test_emptying_the_trash_removes_the_files(admin_client, session, settings, tmp_path):
    path = tmp_path / "vieux.mkv"
    path.write_bytes(b"x")
    session.add(TrashEntry(original_path="/a", trashed_path=str(path)))
    session.commit()

    assert admin_client.delete("/api/trash").status_code == 204
    assert not os.path.exists(path)
    session.expire_all()
    assert session.exec(select(TrashEntry)).all() == []


def test_settings_are_bounded(admin_client, settings, session):
    from app.models.settings import Settings

    saved = admin_client.put("/api/trash/settings", json={"enabled": True, "retention_days": 30}).json()
    assert saved == {"enabled": True, "retention_days": 30, "min_days": 1, "max_days": 90}
    assert admin_client.put("/api/trash/settings", json={"enabled": True, "retention_days": 0}).status_code == 422
    assert admin_client.put("/api/trash/settings", json={"enabled": True, "retention_days": 400}).status_code == 422
    session.expire_all()
    assert session.get(Settings, 1).trash_retention_days == 30
