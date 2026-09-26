"""Saisons téléchargées d'avance, pas encore importées (issue #41) : ni
orphelines, ni proposées au nettoyage, et signalées par un statut informatif.
Un vrai orphelin — ancienne version d'épisodes présents — le reste."""

from sqlmodel import select

from app.models.media import Media, MediaFile, MediaType, Torrent
from app.services.automations import _concerned_torrents
from app.services.cascade_delete import build_delete_preview
from app.services.scan.statuses import compute_statuses, is_healthy
from app.services.torrent_match import FetchedTorrents, MediaView, add_fetched, attach_torrents


def _write(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _fetch(torrent_dir, names_and_sizes, torrent_hash):
    for name, size in names_and_sizes:
        _write(torrent_dir / name, size)
    raw = {"hash": torrent_hash, "name": torrent_dir.name, "save_path": str(torrent_dir), "size": 1}
    fetched = FetchedTorrents()
    add_fetched(fetched, raw, [], [{"name": name, "size": size} for name, size in names_and_sizes])
    return fetched


def _series_view(files, media_type=MediaType.series):
    return MediaView(media_type=media_type, title="Dark", year=2017, alt_titles=[], root_path=None, files=files)


def _classify(settings, view, fetched):
    [torrent] = attach_torrents(settings, [view], fetched, {fetched.rows[0].hash.lower(): 0})[0]
    return torrent


def test_seasons_absent_from_the_library_are_not_orphans(settings, tmp_path, portable_inodes):
    library = [MediaFile(media_id=0, path=str(_write(tmp_path / "lib" / "S01E01.mkv", 10)), episode_label="S01E01")]
    fetched = _fetch(tmp_path / "Dark.S02", [("Dark.S02E01.mkv", 20), ("Dark.S02E02.mkv", 20)], "aaa")

    torrent = _classify(settings, _series_view(library), fetched)

    assert (torrent.is_hardlinked, torrent.repairable, torrent.not_imported) == (False, False, True)
    statuses, reclaimable = compute_statuses(library, [torrent], has_emby_item=True)
    assert "orphelin_qbit" not in statuses and "non_importe" in statuses and reclaimable == 0


def test_an_old_version_of_present_episodes_stays_an_orphan(settings, tmp_path, portable_inodes):
    """Ancienne qualité d'un épisode présent : sa saison est dans la
    bibliothèque, c'est un vrai orphelin."""
    library = [MediaFile(media_id=0, path=str(_write(tmp_path / "lib" / "S01E01.mkv", 10)), episode_label="S01E01")]
    fetched = _fetch(tmp_path / "Dark.S01.720p", [("Dark.S01E01.720p.mkv", 7)], "bbb")

    torrent = _classify(settings, _series_view(library), fetched)

    assert torrent.not_imported is False
    assert "orphelin_qbit" in compute_statuses(library, [torrent], has_emby_item=True)[0]


def test_a_torrent_without_episode_numbers_stays_an_orphan(settings, tmp_path, portable_inodes):
    library = [MediaFile(media_id=0, path=str(_write(tmp_path / "lib" / "S01E01.mkv", 10)), episode_label="S01E01")]
    fetched = _fetch(tmp_path / "Dark.Bonus", [("Making.of.mkv", 5)], "ccc")

    assert _classify(settings, _series_view(library), fetched).not_imported is False


def test_a_movie_torrent_is_never_considered_not_imported(settings, tmp_path, portable_inodes):
    library = [MediaFile(media_id=0, path=str(_write(tmp_path / "lib" / "Film.mkv", 10)))]
    fetched = _fetch(tmp_path / "Film.S02", [("Film.S02E01.mkv", 5)], "ddd")

    assert _classify(settings, _series_view(library, MediaType.movie), fetched).not_imported is False


def test_the_information_never_breaks_a_healthy_media():
    protecting = Torrent(hash="p", name="p", is_hardlinked=True)
    ahead = Torrent(hash="n", name="n", is_hardlinked=False, not_imported=True)
    files = [MediaFile(media_id=0, path="/lib/S01E01.mkv", episode_label="S01E01", is_current=True)]

    statuses, _ = compute_statuses(files, [protecting, ahead], has_emby_item=True)

    assert "non_importe" in statuses and is_healthy(statuses)


def test_cleanup_never_proposes_a_torrent_not_yet_imported(session):
    media = Media(media_type=MediaType.series, title="Dark")
    session.add(media)
    session.commit()
    session.add_all(
        [
            Torrent(media_id=media.id, hash="n", name="Dark.S02", is_hardlinked=False, not_imported=True),
            Torrent(media_id=media.id, hash="o", name="Dark.S01.720p", is_hardlinked=False),
        ]
    )
    session.commit()

    preview = build_delete_preview(session, media)

    assert [item.label for item in preview.items] == ["Dark.S01.720p"]
    assert len(session.exec(select(Torrent)).all()) == 2


def test_orphan_automations_leave_torrents_not_yet_imported_alone():
    ahead = Torrent(hash="n", name="n", is_hardlinked=False, not_imported=True)
    orphan = Torrent(hash="o", name="o", is_hardlinked=False)

    assert _concerned_torrents("orphan_detected", [ahead, orphan]) == [orphan]
