import os

from app.services.scan import _current_flags, _file_match_strength

MOVIE = "/data/media/movies/Matrix (1999)/Matrix.mkv"


def test_identical_path_is_certain():
    assert _file_match_strength(MOVIE, 100, {"path": MOVIE, "size": 100}) == 2


def test_different_mounts_match_on_file_name_and_size():
    radarr_file = {"path": "/movies/Matrix (1999)/Matrix.mkv", "size": 100}
    duplicate = "/data/media/movies/Matrix (1999)/Matrix.720p.mkv"
    assert _current_flags([(MOVIE, 100), (duplicate, 50)], radarr_file) == [True, False]


def test_same_name_with_another_size_is_not_the_tracked_file():
    assert _file_match_strength(MOVIE, 100, {"path": "/movies/Matrix (1999)/Matrix.mkv", "size": 999}) == 0


def test_unknown_size_requires_the_same_parent_folder():
    episode = "/data/media/tv/Show/Season 01/Show - S01E01.mkv"
    assert _file_match_strength(episode, None, {"path": "/tv/Show/Season 01/Show - S01E01.mkv", "size": 10}) == 1
    assert _file_match_strength(episode, None, {"path": "/tv/Other/Show - S01E01.mkv", "size": 10}) == 0


def test_ambiguous_probable_matches_mark_nothing():
    candidates = [("/data/a/Matrix (1999)/Matrix.mkv", 100), ("/data/b/Matrix (1999)/Matrix.mkv", 100)]
    assert _current_flags(candidates, {"path": "/movies/Matrix (1999)/Matrix.mkv", "size": 100}) == [False, False]


def test_certain_match_wins_over_probable_ones(tmp_path):
    source = tmp_path / "library" / "Matrix.mkv"
    source.parent.mkdir()
    source.write_bytes(b"x")
    copy = tmp_path / "other" / "Matrix.mkv"
    copy.parent.mkdir()
    copy.write_bytes(b"x")
    linked = tmp_path / "arr" / "Matrix.mkv"
    linked.parent.mkdir()
    os.link(source, linked)

    arr_file = {"path": str(linked), "size": 1}
    assert _current_flags([(str(copy), 1), (str(source), 1)], arr_file) == [False, True]


def test_resolvable_paths_with_different_inodes_never_match(tmp_path):
    a = tmp_path / "a" / "Matrix.mkv"
    b = tmp_path / "b" / "Matrix.mkv"
    for path in (a, b):
        path.parent.mkdir()
        path.write_bytes(b"x")
    assert _file_match_strength(str(a), 1, {"path": str(b), "size": 1}) == 0


def test_no_tracked_file_marks_nothing():
    assert _current_flags([(MOVIE, 100)], None) == [False]
