"""Analyse d'un seul média : même résultat qu'un scan complet pour ce média,
sans toucher aux autres et sans changer l'identifiant de la fiche."""

import asyncio
import os

import httpx
import pytest
from sqlmodel import Session, select

from app.database import engine
from app.models.media import ImportIssue, Media, MediaFile, MediaType, Torrent
from app.services.media_rescan import rescan_media

LIBRARY_INODE = (222, 1)
MOVIE_PATH = "/data/media/movies/Matrix (1999)/Matrix.mkv"
TORRENT_FILE = os.path.join("/data/torrents/Matrix", "Matrix.mkv")


@pytest.fixture
def fake_inodes(monkeypatch):
    table: dict[str, tuple[int, int]] = {}

    def resolve(path):
        return table.get(path) if path else None

    for module in ("app.services.torrent_match", "app.services.media_rescan", "app.services.scan"):
        monkeypatch.setattr(f"{module}.stat_inode", resolve)
    return table


def radarr_handler(movie: dict | None, queue: list[dict] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v3/queue":
            return httpx.Response(200, json={"records": queue or [], "totalRecords": len(queue or [])})
        if path.startswith("/api/v3/movie/"):
            return httpx.Response(200, json=movie) if movie else httpx.Response(404, json={})
        if path == "/api/v3/movie":
            return httpx.Response(200, json=[movie] if movie else [])
        return httpx.Response(404)

    return handler


def emby_handler(items: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items":
            wanted = request.url.params.get("Ids")
            if wanted:
                return httpx.Response(200, json={"Items": [i for i in items if i["Id"] in wanted.split(",")]})
            return httpx.Response(200, json={"Items": items})
        if request.url.path == "/Users":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    return handler


def qbit_handler(torrents: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=abc; path=/"})
        if path == "/api/v2/torrents/info":
            return httpx.Response(200, json=torrents)
        if path == "/api/v2/torrents/trackers":
            return httpx.Response(200, json=[{"url": "https://tracker.example/announce", "status": 2}])
        if path == "/api/v2/torrents/files":
            wanted = request.url.params.get("hash")
            for t in torrents:
                if t["hash"] == wanted:
                    return httpx.Response(200, json=[{"name": "Matrix.mkv", "size": t["size"]}])
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    return handler


MOVIE = {
    "id": 1,
    "title": "Matrix",
    "year": 1999,
    "path": "/data/media/movies/Matrix (1999)",
    "tmdbId": 603,
    "hasFile": True,
    "movieFile": {"id": 11, "path": MOVIE_PATH, "size": 10},
}
EMBY_MOVIE = {
    "Id": "emby-1",
    "ProviderIds": {"Tmdb": "603"},
    "ImageTags": {"Primary": "tag"},
    "MediaSources": [{"Path": MOVIE_PATH, "Size": 10}],
}
TORRENT = {
    "hash": "AAAA",
    "name": "Matrix.1999.1080p",
    "save_path": "/data/torrents/Matrix",
    "content_path": TORRENT_FILE,
    "size": 10,
    "ratio": 1.0,
    "num_seeds": 5,
    "num_leechs": 0,
    "added_on": 1_700_000_000,
    "completion_on": 1_700_000_100,
    "category": "films",
}


def seed(session: Session) -> tuple[Media, Media]:
    movie = Media(
        media_type=MediaType.movie,
        title="Matrix",
        year=1999,
        radarr_id=1,
        tmdb_id=603,
        emby_item_id="emby-1",
        root_path="/data/media/movies/Matrix (1999)",
        statuses="manquant_qbit",
    )
    other = Media(media_type=MediaType.series, title="Dark", year=2017, sonarr_id=9, emby_item_id="emby-2")
    session.add(movie)
    session.add(other)
    session.commit()
    session.refresh(movie)
    session.refresh(other)
    session.add(
        MediaFile(
            media_id=movie.id,
            path=MOVIE_PATH,
            size=10,
            inode=LIBRARY_INODE[0],
            device=LIBRARY_INODE[1],
            is_current=True,
            arr_file_id=11,
        )
    )
    session.commit()
    return movie, other


def test_a_new_torrent_is_detected_and_the_media_id_never_changes(fake_http, fake_inodes, session, settings):
    movie, other = seed(session)
    movie_id, other_id = movie.id, other.id
    fake_inodes[MOVIE_PATH] = LIBRARY_INODE
    fake_inodes[TORRENT_FILE] = LIBRARY_INODE
    fake_http["http://radarr"] = radarr_handler(MOVIE)
    fake_http["http://emby"] = emby_handler([EMBY_MOVIE])
    fake_http["http://qbit"] = qbit_handler([TORRENT])

    result = asyncio.run(rescan_media(session, settings, movie))

    assert result.media_deleted is False
    assert (result.files, result.torrents) == (1, 1)
    torrent = session.exec(select(Torrent)).one()
    assert torrent.media_id == movie_id and torrent.is_hardlinked is True
    refreshed = session.get(Media, movie_id)
    assert refreshed is not None and "manquant_qbit" not in refreshed.statuses.split(",")
    # L'autre média n'a pas été touché.
    assert session.get(Media, other_id) is not None


def test_a_torrent_of_another_media_is_never_stolen(fake_http, fake_inodes, session, settings):
    movie, other = seed(session)
    session.add(
        Torrent(
            media_id=other.id,
            hash="AAAA",
            name="Matrix.1999.1080p",
            save_path="/data/torrents/Matrix",
            content_path=TORRENT_FILE,
            size=10,
            trackers_json="[]",
        )
    )
    session.commit()
    fake_inodes[MOVIE_PATH] = LIBRARY_INODE
    fake_http["http://radarr"] = radarr_handler(MOVIE)
    fake_http["http://emby"] = emby_handler([EMBY_MOVIE])
    fake_http["http://qbit"] = qbit_handler([TORRENT])

    asyncio.run(rescan_media(session, settings, movie))

    torrent = session.exec(select(Torrent)).one()
    assert torrent.media_id == other.id


def test_a_library_file_removed_from_the_media_server_disappears(fake_http, fake_inodes, session, settings):
    movie, _ = seed(session)
    fake_http["http://radarr"] = radarr_handler(MOVIE)
    # Le serveur multimédia ne connaît plus ce film.
    fake_http["http://emby"] = emby_handler([])
    fake_http["http://qbit"] = qbit_handler([])

    result = asyncio.run(rescan_media(session, settings, movie))

    assert result.files == 0
    assert list(session.exec(select(MediaFile)).all()) == []
    refreshed = session.get(Media, movie.id)
    assert refreshed.emby_item_id is None
    assert "manquant_emby" in refreshed.statuses.split(",")


def test_a_media_removed_from_radarr_loses_its_page(fake_http, fake_inodes, session, settings):
    movie, other = seed(session)
    movie_id, other_id = movie.id, other.id
    fake_http["http://radarr"] = radarr_handler(None)

    result = asyncio.run(rescan_media(session, settings, movie))

    assert result.media_deleted is True
    assert session.get(Media, movie_id) is None
    assert list(session.exec(select(MediaFile)).all()) == []
    assert session.get(Media, other_id) is not None


def test_a_blocked_import_is_reported_on_the_media(fake_http, fake_inodes, session, settings):
    movie, _ = seed(session)
    queue = [
        {
            "id": 7,
            "movieId": 1,
            "downloadId": "ZZZZ",
            "title": "Matrix.2160p-GROUP",
            "trackedDownloadState": "importBlocked",
            "trackedDownloadStatus": "warning",
            "statusMessages": [{"title": "Matrix", "messages": ["Not an upgrade"]}],
        }
    ]
    fake_inodes[MOVIE_PATH] = LIBRARY_INODE
    fake_http["http://radarr"] = radarr_handler(MOVIE, queue)
    fake_http["http://emby"] = emby_handler([EMBY_MOVIE])
    fake_http["http://qbit"] = qbit_handler([])

    result = asyncio.run(rescan_media(session, settings, movie))

    assert result.import_issues == 1
    issue = session.exec(select(ImportIssue)).one()
    assert issue.media_id == movie.id
    assert "import_rate" in session.get(Media, movie.id).statuses.split(",")


def test_torrent_details_are_only_requested_for_candidates(fake_http, fake_inodes, session, settings):
    """Ce qui rend l'analyse d'un média rapide : les fichiers et trackers ne
    sont demandés que pour les torrents susceptibles de le concerner."""
    movie, other = seed(session)
    unrelated = {**TORRENT, "hash": "ZZZZ", "name": "Dark.S01E01.1080p", "content_path": "/data/torrents/Dark/e1.mkv"}
    session.add(Torrent(media_id=other.id, hash="ZZZZ", name=unrelated["name"], size=10, trackers_json="[]"))
    session.commit()

    asked: list[str] = []

    def qbit(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/api/v2/torrents/files", "/api/v2/torrents/trackers"):
            asked.append(request.url.params.get("hash"))
        return qbit_handler([TORRENT, unrelated])(request)

    fake_inodes[MOVIE_PATH] = LIBRARY_INODE
    fake_http["http://radarr"] = radarr_handler(MOVIE)
    fake_http["http://emby"] = emby_handler([EMBY_MOVIE])
    fake_http["http://qbit"] = qbit

    asyncio.run(rescan_media(session, settings, movie))

    assert set(asked) == {"AAAA"}
