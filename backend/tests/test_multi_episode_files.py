"""Fichiers multi-épisodes : un seul item côté serveur multimédia, plusieurs
épisodes côté Sonarr (bug réel : « S03E02 absent d'Emby » sur Under the Dome,
dont les épisodes 1 et 2 sont fusionnés en un fichier)."""

from app.services.scan import _episode_span_labels, _missing_emby_labels


def episode(season: int, number: int, end: int | None = None) -> dict:
    item = {"Id": "1", "ParentIndexNumber": season, "IndexNumber": number}
    if end is not None:
        item["IndexNumberEnd"] = end
    return item


def test_single_episode_covers_itself():
    assert _episode_span_labels(episode(3, 1)) == {"S03E01"}


def test_merged_file_covers_the_whole_range():
    assert _episode_span_labels(episode(3, 1, 2)) == {"S03E01", "S03E02"}
    assert _episode_span_labels(episode(1, 5, 7)) == {"S01E05", "S01E06", "S01E07"}


def test_absurd_range_is_ignored():
    # Borne inversée ou démesurée : on ne masque pas des épisodes réellement absents.
    assert _episode_span_labels(episode(3, 5, 2)) == {"S03E05"}
    assert _episode_span_labels(episode(3, 1, 999)) == {"S03E01"}


def test_unnumbered_episode_keeps_its_item_label():
    assert _episode_span_labels({"Id": "42"}) == {"item:42"}


def test_merged_file_does_not_report_its_second_episode_as_missing():
    spans = {"S03E01": {"S03E01", "S03E02"}}
    downloaded = {"S03E01", "S03E02", "S03E03"}
    assert _missing_emby_labels(downloaded, ["S03E01"], spans) == ["S03E03"]


def test_episode_without_any_file_is_still_reported():
    assert _missing_emby_labels({"S01E01", "S01E02"}, ["S01E01"], {}) == ["S01E02"]


def test_nothing_missing_when_every_episode_is_covered():
    assert _missing_emby_labels({"S01E01"}, ["S01E01", None], {}) == []
