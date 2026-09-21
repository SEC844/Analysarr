"""Analyses partielles : chaque périmètre ne relit qu'une source, laisse le
reste du cache intact et recalcule des statuts justes."""

import asyncio
import os

import httpx
import pytest
from sqlmodel import Session, select

from app.database import engine
from app.models.media import ImportIssue, Media, MediaFile, MediaType, MediaWatch, ScanRun, Torrent
from app.services.partial_scan import run_narrow_scan

# Inodes simulés : sous Windows, `st_dev` dépasse ce que SQLite accepte, et un
# test ne doit de toute façon pas dépendre du système de fichiers réel.
LIBRARY_INODE = (111, 1)


@pytest.fixture
def fake_inodes(monkeypatch):
    """`fake_inodes["/chemin"] = (inode, device)` — tout chemin absent est
    considéré comme non résolu, comme un montage manquant."""
    table: dict[str, tuple[int, int]] = {}

    def resolve(path):
        return table.get(path) if path else None

    for module in ("app.services.torrent_match", "app.services.media_rescan"):
        monkeypatch.setattr(f"{module}.stat_inode", resolve)
    return table


def qbit_handler(torrents: list[dict]):
    """Faux qBittorrent : login, liste des torrents, trackers et fichiers."""

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
                    return httpx.Response(200, json=[{"name": t["name"] + ".mkv", "size": t["size"]}])
        return httpx.Response(404)

    return handler


MOVIE_PATH = "/data/media/movies/Matrix (1999)/Matrix.mkv"


def seed_library(tmp_path) -> tuple[int, int]:
    """Deux médias : un film avec son fichier de bibliothèque, une série sans
    fichier. Renvoie leurs identifiants."""
    with Session(engine) as session:
        movie = Media(
            media_type=MediaType.movie,
            title="Matrix",
            year=1999,
            radarr_id=1,
            emby_item_id="emby-1",
            root_path="/data/media/movies/Matrix (1999)",
        )
        series = Media(media_type=MediaType.series, title="Dark", year=2017, sonarr_id=2, emby_item_id="emby-2")
        session.add(movie)
        session.add(series)
        session.commit()
        session.refresh(movie)
        session.refresh(series)
        session.add(
            MediaFile(
                media_id=movie.id,
                path=MOVIE_PATH,
                size=10,
                inode=LIBRARY_INODE[0],
                device=LIBRARY_INODE[1],
                is_current=True,
            )
        )
        session.commit()
        return movie.id, series.id


def test_torrent_scope_detects_a_new_torrent_and_keeps_media_ids(fake_http, fake_inodes, settings, tmp_path):
    movie_id, series_id = seed_library(tmp_path)
    # `os.path.join` sépare selon l'OS du test : on enregistre le chemin tel
    # que le code le construira.
    fake_inodes[os.path.join("/data/torrents/Matrix", "Matrix.mkv")] = LIBRARY_INODE
    fake_http["http://qbit"] = qbit_handler(
        [
            {
                "hash": "AAAA",
                "name": "Matrix",
                "save_path": "/data/torrents/Matrix",
                "content_path": "/data/torrents/Matrix/Matrix.mkv",
                "size": 10,
                "ratio": 1.0,
                "num_seeds": 2,
                "num_leechs": 0,
                "added_on": 1_700_000_000,
                "completion_on": 1_700_000_100,
                "category": "films",
            }
        ]
    )

    asyncio.run(run_narrow_scan("torrents"))

    with Session(engine) as session:
        torrents = list(session.exec(select(Torrent)).all())
        assert [t.hash for t in torrents] == ["AAAA"]
        assert torrents[0].media_id == movie_id
        # Fichier de bibliothèque et torrent partagent l'inode : média protégé.
        assert torrents[0].is_hardlinked is True
        movie = session.get(Media, movie_id)
        assert "manquant_qbit" not in movie.statuses.split(",")
        # La série n'a pas bougé et garde son identifiant.
        assert session.get(Media, series_id) is not None
        run = session.exec(select(ScanRun)).first()
        assert run.scope == "torrents" and run.status.value == "completed"
        assert run.qbittorrent_torrent_count == 1 and run.qbittorrent_matched_count == 1


def test_torrent_scope_never_steals_a_torrent_from_another_media(fake_http, fake_inodes, settings, tmp_path):
    movie_id, series_id = seed_library(tmp_path)
    with Session(engine) as session:
        # Torrent rattaché à la série par l'historique Sonarr lors d'un scan
        # complet, alors que son nom ressemble au titre du film.
        session.add(
            Torrent(
                media_id=series_id,
                hash="BBBB",
                name="Matrix.S01E01.1080p",
                save_path="/elsewhere",
                content_path="/elsewhere/Matrix.S01E01.mkv",
                size=42,
                trackers_json="[]",
            )
        )
        session.commit()

    fake_http["http://qbit"] = qbit_handler(
        [
            {
                "hash": "BBBB",
                "name": "Matrix.S01E01.1080p",
                "save_path": "/elsewhere",
                "content_path": "/elsewhere/Matrix.S01E01.mkv",
                "size": 42,
                "ratio": 0.5,
                "num_seeds": 1,
                "num_leechs": 0,
                "added_on": 1_700_000_000,
                "completion_on": 0,
                "category": None,
            }
        ]
    )

    asyncio.run(run_narrow_scan("torrents"))

    with Session(engine) as session:
        torrent = session.exec(select(Torrent)).one()
        assert torrent.media_id == series_id


def test_torrent_scope_drops_a_torrent_removed_from_the_client(fake_http, fake_inodes, settings, tmp_path):
    movie_id, _ = seed_library(tmp_path)
    with Session(engine) as session:
        session.add(Torrent(media_id=movie_id, hash="OLD", name="Matrix", size=10, trackers_json="[]"))
        session.commit()

    fake_http["http://qbit"] = qbit_handler([])
    asyncio.run(run_narrow_scan("torrents"))

    with Session(engine) as session:
        assert list(session.exec(select(Torrent)).all()) == []
        movie = session.get(Media, movie_id)
        # Plus aucun torrent : le média redevient « non seedé ».
        assert "manquant_qbit" in movie.statuses.split(",")


def test_queue_scope_refreshes_import_issues_and_statuses(fake_http, fake_inodes, settings, tmp_path):
    movie_id, _ = seed_library(tmp_path)

    def radarr(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/queue":
            return httpx.Response(
                200,
                json={
                    "records": [
                        {
                            "id": 5,
                            "movieId": 1,
                            "downloadId": "AAAA",
                            "title": "Matrix.2160p-GROUP",
                            "trackedDownloadState": "importBlocked",
                            "trackedDownloadStatus": "warning",
                            "statusMessages": [{"title": "Matrix", "messages": ["Not an upgrade"]}],
                        }
                    ],
                    "totalRecords": 1,
                },
            )
        return httpx.Response(404)

    fake_http["http://radarr"] = radarr
    fake_http["http://sonarr"] = lambda request: httpx.Response(200, json={"records": [], "totalRecords": 0})

    asyncio.run(run_narrow_scan("queue"))

    with Session(engine) as session:
        issue = session.exec(select(ImportIssue)).one()
        assert issue.media_id == movie_id and issue.kind == "import"
        assert "import_rate" in session.get(Media, movie_id).statuses.split(",")


def test_watch_scope_replaces_watch_rows_without_touching_statuses(fake_http, fake_inodes, settings, tmp_path):
    movie_id, _ = seed_library(tmp_path)
    with Session(engine) as session:
        movie = session.get(Media, movie_id)
        movie.statuses = "doublon"
        session.add(movie)
        session.add(MediaWatch(media_id=movie_id, emby_user_id="old", played=True))
        session.commit()

    def emby(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/Users":
            return httpx.Response(200, json=[{"Id": "u1", "Name": "Marie", "Policy": {}}])
        if path == "/Users/u1/Items":
            # Le film est vu par cet utilisateur ; aucune série ni épisode.
            if request.url.params.get("IncludeItemTypes") == "Movie":
                item = {"Id": "emby-1", "UserData": {"Played": True, "PlayedPercentage": 100}}
                return httpx.Response(200, json={"Items": [item]})
            return httpx.Response(200, json={"Items": []})
        return httpx.Response(404)

    fake_http["http://emby"] = emby
    asyncio.run(run_narrow_scan("watch"))

    with Session(engine) as session:
        rows = list(session.exec(select(MediaWatch)).all())
        assert [r.emby_user_id for r in rows] == ["u1"]
        # Un scan de visionnage ne change aucun statut.
        assert session.get(Media, movie_id).statuses == "doublon"


def test_seer_scope_requires_seer_to_be_enabled(fake_http, fake_inodes, settings, tmp_path):
    seed_library(tmp_path)
    asyncio.run(run_narrow_scan("seer"))

    with Session(engine) as session:
        run = session.exec(select(ScanRun)).one()
        assert run.status.value == "failed"
        assert "Seer" in (run.error_message or "")


def test_a_failing_source_never_wipes_the_cache(fake_http, fake_inodes, settings, tmp_path):
    """Client torrent injoignable : l'analyse échoue proprement et les torrents
    connus restent en base."""
    movie_id, _ = seed_library(tmp_path)
    with Session(engine) as session:
        session.add(Torrent(media_id=movie_id, hash="KEEP", name="Matrix", size=10, trackers_json="[]"))
        session.commit()

    asyncio.run(run_narrow_scan("torrents"))  # aucun hôte déclaré : injoignable

    with Session(engine) as session:
        assert [t.hash for t in session.exec(select(Torrent)).all()] == ["KEEP"]
        assert session.exec(select(ScanRun)).one().status.value == "failed"


@pytest.mark.parametrize("scope", ["torrents", "queue", "watch", "seer"])
def test_every_narrow_scope_records_its_run(fake_http, fake_inodes, settings, tmp_path, scope):
    seed_library(tmp_path)
    asyncio.run(run_narrow_scan(scope))
    with Session(engine) as session:
        assert session.exec(select(ScanRun)).one().scope == scope
