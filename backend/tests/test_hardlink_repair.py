"""Réparation de hardlink : la seule action qui écrit à la place de
l'utilisateur sur ses fichiers de bibliothèque.

Bug réel : `torrent_client` n'était plus importé dans `hardlink_repair.py` —
l'aperçu comme l'exécution échouaient sur un `NameError`, bouton « Réparer les
hardlinks » et automatisation de réparation compris."""

import asyncio
import os

import pytest

from app.models.media import Media, MediaFile, MediaType, Torrent
from app.services.hardlink import stat_inode
from app.services.hardlink_repair import build_repair_preview, execute_repair


class FakeTorrentClient:
    """Rend les fichiers déclarés pour chaque hash, comme le client réel."""

    def __init__(self, files: dict[str, list[dict]]):
        self.files = files

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def get_files(self, torrent_hash):
        return self.files.get(torrent_hash, [])


@pytest.fixture
def library(session, settings, tmp_path, monkeypatch):
    """Un film présent dans la bibliothèque et une copie séparée côté torrent :
    même contenu, deux inodes — exactement ce que la réparation doit réunir."""
    root = tmp_path / "media"
    (root / "Matrix (1999)").mkdir(parents=True)
    downloads = tmp_path / "torrents" / "Matrix"
    downloads.mkdir(parents=True)
    library_file = root / "Matrix (1999)" / "Matrix.mkv"
    torrent_file = downloads / "Matrix.mkv"
    library_file.write_bytes(b"film")
    torrent_file.write_bytes(b"film")

    settings.emby_library_path = str(root)
    session.add(settings)
    media = Media(media_type=MediaType.movie, title="Matrix", year=1999, radarr_id=1)
    session.add(media)
    session.commit()
    session.refresh(media)
    row = MediaFile(media_id=media.id, path=str(library_file), size=4, is_current=True)
    torrent = Torrent(
        media_id=media.id,
        hash="AAAA",
        name="Matrix.1999",
        save_path=str(downloads),
        content_path=str(torrent_file),
        size=4,
        is_hardlinked=False,
        repairable=True,
        trackers_json="[]",
    )
    session.add(row)
    session.add(torrent)
    session.commit()
    session.refresh(row)
    session.refresh(torrent)

    client = FakeTorrentClient({"AAAA": [{"name": "Matrix.mkv", "size": 4}]})
    monkeypatch.setattr("app.services.hardlink_repair.torrent_client", lambda s: client)
    # Un `st_dev` Windows dépasse l'entier signé de SQLite ; seule l'égalité des
    # couples (inode, device) compte ici, pas la valeur du device lui-même.
    for module in ("app.services.hardlink_repair", "app.services.scan"):
        monkeypatch.setattr(f"{module}.stat_inode", _portable_stat_inode)
    return media, row, torrent, client


def _portable_stat_inode(path: str) -> tuple[int, int] | None:
    got = stat_inode(path)
    return None if got is None else (got[0], got[1] % 2**31)


def inode(path: str) -> int:
    return os.stat(path).st_ino


def test_the_preview_pairs_the_library_file_with_the_torrent(library, session, settings):
    media, row, torrent, _client = library

    preview = asyncio.run(build_repair_preview(session, media, settings))

    assert len(preview.items) == 1 and preview.unmatched_torrents == []
    item = preview.items[0]
    assert item.direction == "torrent_to_library"
    assert (item.target_path, item.source_path) == (row.path, torrent.content_path)


def test_repairing_puts_both_files_in_the_same_hardlink_group(library, session, settings):
    media, row, torrent, _client = library
    assert inode(row.path) != inode(torrent.content_path)

    result = asyncio.run(execute_repair(session, media, settings))

    assert [step.success for step in result.steps] == [True]
    assert inode(row.path) == inode(torrent.content_path)
    assert session.get(Torrent, torrent.id).is_hardlinked is True
    assert "orphelin_qbit" not in (session.get(Media, media.id).statuses or "").split(",")


def test_a_library_file_already_protected_is_never_touched(library, session, settings, tmp_path):
    """La bibliothèque est déjà hardlinkée à un autre torrent : c'est la copie
    séparée qui doit être remplacée, jamais le lien qui fonctionne déjà."""
    media, row, torrent, client = library
    protected_path = tmp_path / "torrents" / "Matrix.copie.mkv"
    os.link(row.path, protected_path)
    session.add(
        Torrent(
            media_id=media.id,
            hash="BBBB",
            name="Matrix.1999.copie",
            save_path=str(tmp_path / "torrents"),
            content_path=str(protected_path),
            size=4,
            is_hardlinked=True,
            trackers_json="[]",
        )
    )
    session.commit()
    client.files["BBBB"] = [{"name": "Matrix.copie.mkv", "size": 4}]
    library_inode = inode(row.path)

    preview = asyncio.run(build_repair_preview(session, media, settings))
    assert [item.direction for item in preview.items] == ["library_to_torrent"]

    result = asyncio.run(execute_repair(session, media, settings))

    assert [step.success for step in result.steps] == [True]
    assert inode(row.path) == library_inode  # le lien existant n'a pas bougé
    assert inode(torrent.content_path) == library_inode
