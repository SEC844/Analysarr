"""Médias présents sur le serveur multimédia mais suivis par aucun
Sonarr/Radarr : ils étaient totalement invisibles jusqu'ici, doublons et
torrents orphelins compris."""

import asyncio
import os

import httpx
from sqlmodel import select

from app.models.media import Media, MediaFile, MediaType
from app.services.scan import build_untracked_results, compute_statuses, is_tracked_by_arr


class FakeEmby:
    """Serveur multimédia minimal : seuls les épisodes sont demandés en plus
    des listes déjà chargées par le scan."""

    def __init__(self, episodes: dict[str, list[dict]] | None = None):
        self.episodes = episodes or {}
        self.asked: list[str] = []

    async def get_episodes(self, item_id: str) -> list[dict]:
        self.asked.append(item_id)
        return self.episodes.get(item_id, [])


def movie_item(item_id: str, name: str, path: str, *, tmdb: str = "", imdb: str = "") -> dict:
    providers = {}
    if tmdb:
        providers["Tmdb"] = tmdb
    if imdb:
        providers["Imdb"] = imdb
    return {
        "Id": item_id,
        "Name": name,
        "ProductionYear": 2024,
        "ProviderIds": providers,
        "ImageTags": {"Primary": "tag"},
        "MediaSources": [{"Path": path, "Size": 10}],
    }


def episode_item(path: str, season: int, number: int) -> dict:
    return {
        "Id": f"ep-{season}-{number}",
        "ParentIndexNumber": season,
        "IndexNumber": number,
        "MediaSources": [{"Path": path, "Size": 5}],
    }


def context(emby: FakeEmby):
    from app.services.scan import LibraryContext

    return LibraryContext(emby=emby)


def test_a_movie_absent_from_radarr_becomes_a_media(tmp_path):
    path = str(tmp_path / "Film.mkv")
    emby = FakeEmby()
    items = [movie_item("m1", "Film hors Radarr", path, tmdb="42", imdb="tt42")]

    results = asyncio.run(build_untracked_results(context(emby), items, [], claimed_item_ids=set()))

    assert len(results) == 1
    media = results[0].media
    assert media.title == "Film hors Radarr" and media.media_type == MediaType.movie
    assert media.radarr_id is None and not is_tracked_by_arr(media)
    # Identifiants repris du serveur multimédia : c'est avec eux que
    # l'utilisateur ajoutera le film dans Radarr.
    assert (media.tmdb_id, media.imdb_id) == (42, "tt42")
    assert [f.path for f in results[0].files] == [path]
    assert results[0].files[0].is_current and results[0].files[0].arr_file_id is None
    assert results[0].root_path == str(tmp_path)


def test_an_item_already_matched_to_an_arr_media_is_left_alone(tmp_path):
    emby = FakeEmby()
    items = [movie_item("m1", "Film suivi", str(tmp_path / "Film.mkv"), tmdb="42")]

    results = asyncio.run(build_untracked_results(context(emby), items, [], claimed_item_ids={"m1"}))

    assert results == []


def test_a_series_absent_from_sonarr_keeps_its_episodes(tmp_path):
    first, second = str(tmp_path / "S01E01.mkv"), str(tmp_path / "S01E02.mkv")
    emby = FakeEmby({"s1": [episode_item(first, 1, 1), episode_item(second, 1, 2)]})
    series = [
        {
            "Id": "s1",
            "Name": "Série hors Sonarr",
            "ProductionYear": 2020,
            "ProviderIds": {"Tvdb": "99"},
            "ImageTags": {"Primary": "tag"},
        }
    ]

    results = asyncio.run(build_untracked_results(context(emby), [], series, claimed_item_ids=set()))

    assert len(results) == 1 and emby.asked == ["s1"]
    media, files = results[0].media, results[0].files
    assert media.media_type == MediaType.series and media.tvdb_id == 99 and media.episode_count == 2
    assert sorted(f.episode_label for f in files) == ["S01E01", "S01E02"]
    assert sorted(f.path for f in files) == sorted([first, second])


def test_an_item_without_file_is_ignored():
    """Une fiche sans fichier (série jamais téléchargée, film supprimé du
    disque) n'a rien à analyser : elle n'encombre pas la bibliothèque."""
    emby = FakeEmby({"s1": []})
    items = [{"Id": "m1", "Name": "Sans fichier", "MediaSources": []}]
    series = [{"Id": "s1", "Name": "Sans épisode"}]

    assert asyncio.run(build_untracked_results(context(emby), items, series, claimed_item_ids=set())) == []


def test_status_says_untracked_and_never_missing_from_the_media_server():
    files = [MediaFile(media_id=0, path="/data/media/Film.mkv", size=10, is_current=True)]

    statuses, _ = compute_statuses(files, [], has_emby_item=True, tracked_by_arr=False)

    assert "manquant_arr" in statuses
    assert "manquant_emby" not in statuses  # il EST dans la bibliothèque
    # Un média suivi ne porte jamais ce statut.
    tracked, _ = compute_statuses(files, [], has_emby_item=True)
    assert "manquant_arr" not in tracked


def test_untracked_media_survive_a_radarr_scan(session, settings, monkeypatch, tmp_path):
    """Une analyse Radarr ne connaît pas les médias non suivis : elle ne doit
    pas les effacer au passage."""
    from app.services import partial_scan

    library_media = Media(
        media_type=MediaType.movie,
        title="Film hors Radarr",
        emby_item_id="m1",
        statuses="manquant_arr",
    )
    session.add(library_media)
    session.commit()

    monkeypatch.setattr(partial_scan, "_reattach_known_torrents", lambda *args, **kwargs: 0)
    partial_scan._persist_service_results(
        session, settings, results=[], wants_movies=True, wants_series=False, refresh_queue=False
    )

    session.expire_all()
    assert session.exec(select(Media)).one().emby_item_id == "m1"


def test_a_media_server_scan_prunes_them(session, settings, monkeypatch):
    """L'analyse du serveur multimédia, elle, les reconstruit : un média qui
    n'est plus dans la bibliothèque disparaît."""
    from app.services import partial_scan

    session.add(Media(media_type=MediaType.movie, title="Parti", emby_item_id="m1", statuses="manquant_arr"))
    session.commit()

    monkeypatch.setattr(partial_scan, "_reattach_known_torrents", lambda *args, **kwargs: 0)
    partial_scan._persist_service_results(
        session,
        settings,
        results=[],
        wants_movies=True,
        wants_series=False,
        refresh_queue=False,
        prune_untracked=True,
    )

    session.expire_all()
    assert session.exec(select(Media)).all() == []


def test_two_untracked_media_keep_their_own_row(session, settings, monkeypatch):
    """Sans identifiant Radarr, deux médias partageraient la même clé : c'est
    l'item du serveur multimédia qui les distingue."""
    from app.services import partial_scan

    for item_id in ("m1", "m2"):
        session.add(Media(media_type=MediaType.movie, title=f"Film {item_id}", emby_item_id=item_id))
    session.commit()

    keys = {partial_scan._arr_key(media) for media in session.exec(select(Media)).all()}
    assert keys == {("movie", "library", "m1"), ("movie", "library", "m2")}
