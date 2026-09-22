import asyncio
import json
import os

import pytest

from app.models.media import Media, MediaFile, MediaType, Torrent
from app.services.media_delete import build_delete_footprint
from app.services.scan import alert_statuses, compute_statuses, is_healthy


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


def test_a_healthy_media_carries_only_its_tracker_badge():
    """Sain = suivi par Sonarr/Radarr, présent sur le serveur multimédia, et
    protégé par un torrent hardlinké. La couverture tracker est un badge à
    côté, jamais une alerte."""
    files = [MediaFile(path="/a", size=10, inode=1, device=1, is_current=True)]
    second = Torrent(
        hash="h2", name="t2", is_hardlinked=True, trackers_json=json.dumps([{"domain": "b.org", "status": "ok"}])
    )
    statuses, reclaimable = compute_statuses(files, [torrent(is_hardlinked=True), second], True)

    assert statuses == {"cross_seed"} and reclaimable == 0
    assert is_healthy(statuses)
    assert alert_statuses(statuses) == set()


def test_a_single_tracker_no_longer_prevents_a_media_from_being_healthy():
    files = [MediaFile(path="/a", size=10, inode=1, device=1, is_current=True)]

    statuses, _ = compute_statuses(files, [torrent(is_hardlinked=True)], True)

    assert statuses == {"tracker_unique"} and is_healthy(statuses)


def test_the_tracker_badge_ignores_torrents_that_protect_nothing():
    """Les trackers d'un orphelin ne couvrent plus le média : seuls les
    torrents hardlinkés comptent."""
    files = [MediaFile(path="/a", size=10, inode=1, device=1, is_current=True)]
    orphan = Torrent(
        hash="h3",
        name="t3",
        is_hardlinked=False,
        repairable=False,
        trackers_json=json.dumps([{"domain": "b.org", "status": "ok"}]),
    )

    statuses, _ = compute_statuses(files, [torrent(is_hardlinked=True), orphan], True)

    assert "tracker_unique" in statuses and "cross_seed" not in statuses
    # Un orphelin reste une alerte, lui.
    assert not is_healthy(statuses)


def test_a_media_without_hardlinked_torrent_is_never_healthy():
    files = [MediaFile(path="/a", size=10, inode=1, device=1, is_current=True)]

    statuses, _ = compute_statuses(files, [], True)

    assert "manquant_qbit" in statuses and not is_healthy(statuses)


def test_statuses_for_problematic_media():
    duplicates = [MediaFile(path="/a", size=10, inode=1, device=1), MediaFile(path="/b", size=8, inode=2, device=1)]
    statuses, reclaimable = compute_statuses(duplicates, [torrent(is_hardlinked=False, size=5)], has_emby_item=False)
    assert {"doublon", "orphelin_qbit", "tracker_unique", "manquant_emby", "manquant_qbit"} <= statuses
    assert reclaimable == 8 + 5

    repairable_statuses, _ = compute_statuses([MediaFile(path="/a", size=10)], [torrent(is_hardlinked=False, repairable=True)], True)
    assert "non_hardlink" in repairable_statuses and "manquant_qbit" not in repairable_statuses


def test_the_library_filters_combine_statuses(admin_client, session):
    """Issue #35 : croiser plusieurs statuts. `any` (défaut) élargit, `all`
    restreint aux médias qui les portent tous."""
    rows = [
        Media(media_type=MediaType.movie, title="Sain", statuses="cross_seed"),
        Media(media_type=MediaType.movie, title="Doublon seul", statuses="doublon"),
        Media(media_type=MediaType.series, title="Les deux", statuses="doublon,manquant_emby"),
    ]
    session.add_all(rows)
    session.commit()

    def titles(**params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return sorted(item["title"] for item in admin_client.get(f"/api/media?{query}").json()["items"])

    assert titles(status="doublon,manquant_emby") == ["Doublon seul", "Les deux"]
    assert titles(status="doublon,manquant_emby", match="all") == ["Les deux"]
    # Un statut inventé est ignoré, jamais interprété.
    assert titles(status="doublon,rm -rf") == ["Doublon seul", "Les deux"]
    assert titles(health="sain") == ["Sain"]
    assert titles(health="alerte") == ["Doublon seul", "Les deux"]
    assert titles(health="alerte", media_type="series") == ["Les deux"]


def test_the_watch_filter_accepts_several_values(admin_client, session):
    session.add_all(
        [
            Media(media_type=MediaType.movie, title="Jamais vu", watch_user_count=2),
            Media(media_type=MediaType.movie, title="En cours", watch_user_count=2, watch_in_progress_count=1),
            Media(media_type=MediaType.movie, title="Vu par tous", watch_user_count=2, watch_played_count=2),
        ]
    )
    session.commit()

    listed = admin_client.get("/api/media?watch=never,in_progress").json()["items"]
    assert sorted(item["title"] for item in listed) == ["En cours", "Jamais vu"]
