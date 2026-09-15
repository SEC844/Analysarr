import asyncio
import json
import os

import pytest

from app.models.media import Media, MediaFile, MediaType, Torrent
from app.services.media_delete import build_delete_footprint
from app.services.scan import compute_statuses


def test_hardlinked_copies_share_a_single_disk_unit(session, settings, tmp_path):
    library = tmp_path / "S01E01.mkv"
    library.write_bytes(b"x" * 1000)
    torrent_copy = tmp_path / "torrent" / "S01E01.mkv"
    torrent_copy.parent.mkdir()
    os.link(library, torrent_copy)
    other = tmp_path / "S01E02.mkv"
    other.write_bytes(b"y" * 500)

    media = Media(media_type=MediaType.series, title="Série")
    session.add(media)
    session.commit()
    session.add_all(
        [
            MediaFile(media_id=media.id, path=str(library), size=1000),
            MediaFile(media_id=media.id, path=str(other), size=500),
            Torrent(media_id=media.id, hash="h", name="t", content_path=str(torrent_copy), size=1000),
        ]
    )
    session.commit()
    settings.qbittorrent_url = None  # repli sur content_path, sans qBittorrent

    footprint = asyncio.run(build_delete_footprint(session, media, settings))

    assert [(u.size, u.links) for u in footprint.units] == [(1000, 2), (500, 1)]
    assert footprint.torrents[0].units == footprint.files[0].units


def test_deleting_a_symlink_frees_nothing(session, settings, tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"x" * 100)
    link = tmp_path / "link.mkv"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("liens symboliques non autorisés sur cette machine")
    media = Media(media_type=MediaType.movie, title="Film")
    session.add(media)
    session.commit()
    session.add(MediaFile(media_id=media.id, path=str(link), size=100))
    session.commit()

    footprint = asyncio.run(build_delete_footprint(session, media, settings))
    assert footprint.files[0].units == []


def torrent(**fields):
    return Torrent(hash="h", name="t", trackers_json=json.dumps([{"domain": "a.org", "status": "ok"}]), **fields)


def test_healthy_media_has_no_status():
    files = [MediaFile(path="/a", size=10, inode=1, device=1, is_current=True)]
    statuses, reclaimable = compute_statuses(
        files, [torrent(is_hardlinked=True), Torrent(hash="h2", name="t2", is_hardlinked=True, trackers_json=json.dumps([{"domain": "b.org", "status": "ok"}]))], True
    )
    assert statuses == set() and reclaimable == 0


def test_statuses_for_problematic_media():
    duplicates = [MediaFile(path="/a", size=10, inode=1, device=1), MediaFile(path="/b", size=8, inode=2, device=1)]
    statuses, reclaimable = compute_statuses(duplicates, [torrent(is_hardlinked=False, size=5)], has_emby_item=False)
    assert {"doublon", "orphelin_qbit", "tracker_unique", "manquant_emby", "manquant_qbit"} <= statuses
    assert reclaimable == 8 + 5

    repairable_statuses, _ = compute_statuses([MediaFile(path="/a", size=10)], [torrent(is_hardlinked=False, repairable=True)], True)
    assert "non_hardlink" in repairable_statuses and "manquant_qbit" not in repairable_statuses
