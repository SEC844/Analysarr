"""Analyses partielles : chaque périmètre ne relit qu'une source, laisse le
reste du cache intact et recalcule des statuts justes."""

import asyncio
import importlib
import os
import time

import httpx
import pytest
from sqlmodel import Session, select

from app.database import engine
from app.models.media import ImportIssue, Media, MediaFile, MediaType, MediaWatch, ScanRun, ScanStatus, Torrent
from app.models.settings import Settings
from app.services.events import scan_events
from app.services.partial_scan import run_service_scan

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

    for module in ("app.services.torrent_match", "app.services.scan.library"):
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

    asyncio.run(run_service_scan("torrents"))

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

    asyncio.run(run_service_scan("torrents"))

    with Session(engine) as session:
        torrent = session.exec(select(Torrent)).one()
        assert torrent.media_id == series_id


def test_torrent_scope_drops_a_torrent_removed_from_the_client(fake_http, fake_inodes, settings, tmp_path):
    movie_id, _ = seed_library(tmp_path)
    with Session(engine) as session:
        session.add(Torrent(media_id=movie_id, hash="OLD", name="Matrix", size=10, trackers_json="[]"))
        session.commit()

    fake_http["http://qbit"] = qbit_handler([])
    asyncio.run(run_service_scan("torrents"))

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

    asyncio.run(run_service_scan("queue"))

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
    asyncio.run(run_service_scan("watch"))

    with Session(engine) as session:
        rows = list(session.exec(select(MediaWatch)).all())
        assert [r.emby_user_id for r in rows] == ["u1"]
        # Un scan de visionnage ne change aucun statut.
        assert session.get(Media, movie_id).statuses == "doublon"


def test_seer_scope_requires_seer_to_be_enabled(fake_http, fake_inodes, settings, tmp_path):
    seed_library(tmp_path)
    asyncio.run(run_service_scan("seer"))

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

    asyncio.run(run_service_scan("torrents"))  # aucun hôte déclaré : injoignable

    with Session(engine) as session:
        assert [t.hash for t in session.exec(select(Torrent)).all()] == ["KEEP"]
        assert session.exec(select(ScanRun)).one().status.value == "failed"


@pytest.mark.parametrize("scope", ["torrents", "queue", "watch", "seer"])
def test_every_narrow_scope_records_its_run(fake_http, fake_inodes, settings, tmp_path, scope):
    seed_library(tmp_path)
    asyncio.run(run_service_scan(scope))
    with Session(engine) as session:
        assert session.exec(select(ScanRun)).one().scope == scope


# --- Analyses par service (Radarr, Sonarr, serveur multimédia) ---------------

RADARR_MOVIE = {
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


def radarr_handler(movies: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/movie":
            return httpx.Response(200, json=movies)
        if request.url.path == "/api/v3/queue":
            return httpx.Response(200, json={"records": [], "totalRecords": 0})
        return httpx.Response(404)

    return handler


def emby_items_handler(items: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items":
            kinds = request.url.params.get("IncludeItemTypes")
            return httpx.Response(200, json={"Items": items if kinds == "Movie" else []})
        return httpx.Response(404)

    return handler


def test_radarr_scope_keeps_media_ids_and_leaves_series_alone(fake_http, fake_inodes, settings, tmp_path):
    movie_id, series_id = seed_library(tmp_path)
    fake_inodes[MOVIE_PATH] = LIBRARY_INODE
    fake_http["http://radarr"] = radarr_handler([RADARR_MOVIE])
    fake_http["http://emby"] = emby_items_handler([EMBY_MOVIE])

    asyncio.run(run_service_scan("radarr"))

    with Session(engine) as session:
        movie = session.get(Media, movie_id)
        assert movie is not None and movie.title == "Matrix"
        assert [f.path for f in session.exec(select(MediaFile)).all()] == [MOVIE_PATH]
        # La série n'est pas touchée par une analyse Radarr.
        assert session.get(Media, series_id) is not None
        assert session.exec(select(ScanRun)).one().scope == "radarr"


def test_radarr_scope_removes_a_movie_radarr_no_longer_tracks(fake_http, fake_inodes, settings, tmp_path):
    movie_id, series_id = seed_library(tmp_path)
    fake_http["http://radarr"] = radarr_handler([])
    fake_http["http://emby"] = emby_items_handler([])

    asyncio.run(run_service_scan("radarr"))

    with Session(engine) as session:
        assert session.get(Media, movie_id) is None
        assert session.get(Media, series_id) is not None


def test_a_library_scan_reuses_known_torrents_without_calling_the_client(fake_http, fake_inodes, settings, tmp_path):
    """Les fichiers des torrents sont mémorisés : l'état hardlinké est
    recalculé sans aucun appel au client torrent."""
    from app.models.media import TorrentFile

    movie_id, _ = seed_library(tmp_path)
    torrent_file = os.path.join("/data/torrents/Matrix", "Matrix.mkv")
    with Session(engine) as session:
        session.add(
            Torrent(
                media_id=movie_id,
                hash="AAAA",
                name="Matrix.1999.1080p",
                save_path="/data/torrents/Matrix",
                content_path=torrent_file,
                size=10,
                trackers_json="[]",
                is_hardlinked=False,
            )
        )
        session.add(TorrentFile(torrent_hash="aaaa", path=torrent_file, size=10))
        session.commit()

    # Le fichier du torrent partage maintenant l'inode du fichier importé.
    fake_inodes[MOVIE_PATH] = LIBRARY_INODE
    fake_inodes[torrent_file] = LIBRARY_INODE
    fake_http["http://radarr"] = radarr_handler([RADARR_MOVIE])
    fake_http["http://emby"] = emby_items_handler([EMBY_MOVIE])
    # Aucun hôte qBittorrent déclaré : tout appel au client ferait échouer
    # l'analyse, ce qui prouve qu'il n'est pas interrogé.

    asyncio.run(run_service_scan("radarr"))

    with Session(engine) as session:
        torrent = session.exec(select(Torrent)).one()
        assert torrent.media_id == movie_id
        assert torrent.is_hardlinked is True
        assert session.exec(select(ScanRun)).one().status.value == "completed"


def test_media_server_scope_refreshes_files_without_touching_the_queue(fake_http, fake_inodes, settings, tmp_path):
    movie_id, _ = seed_library(tmp_path)
    with Session(engine) as session:
        session.add(ImportIssue(media_id=movie_id, kind="import", title="bloqué", download_id="ZZZZ"))
        session.commit()

    fake_inodes[MOVIE_PATH] = LIBRARY_INODE
    fake_http["http://radarr"] = radarr_handler([RADARR_MOVIE])
    fake_http["http://sonarr"] = lambda request: httpx.Response(200, json=[])
    fake_http["http://emby"] = emby_items_handler([EMBY_MOVIE])

    asyncio.run(run_service_scan("media_server"))

    with Session(engine) as session:
        # L'import bloqué vient de la file d'attente : il est conservé.
        assert len(list(session.exec(select(ImportIssue)).all())) == 1
        assert "import_rate" in session.get(Media, movie_id).statuses.split(",")


def test_an_unconfigured_service_fails_cleanly(fake_http, fake_inodes, settings, tmp_path):
    seed_library(tmp_path)
    with Session(engine) as session:
        row = session.get(Settings, 1)
        row.radarr_url = None
        row.radarr_api_key = None
        session.add(row)
        session.commit()

    asyncio.run(run_service_scan("radarr"))

    with Session(engine) as session:
        run = session.exec(select(ScanRun)).one()
        assert run.status.value == "failed" and "Radarr" in (run.error_message or "")


@pytest.mark.parametrize("trigger, announced", [("manual", True), ("realtime", False)])
def test_only_user_analyses_reach_the_scan_button(fake_http, fake_inodes, settings, tmp_path, trigger, announced):
    """Une analyse du temps réel ne passe pas par le flux du bouton Scanner :
    sa fin fermait le flux d'un scan lancé à la main qui attendait son tour,
    et le bouton restait bloqué sur « Démarrage du scan... » pendant qu'elle
    tournait. Réussite comme échec."""
    seed_library(tmp_path)
    fake_http["http://qbit"] = qbit_handler([])

    async def run() -> list[dict]:
        queue = scan_events.subscribe()
        try:
            await run_service_scan("torrents", trigger=trigger)
            await run_service_scan("seer", trigger=trigger)  # Seer désactivé : échec
            return [queue.get_nowait() for _ in range(queue.qsize())]
        finally:
            scan_events.unsubscribe(queue)

    events = asyncio.run(run())

    types = [event["type"] for event in events]
    assert types == (["started", "progress", "progress", "completed", "started", "failed"] if announced else [])


def test_the_scan_button_ignores_realtime_runs(admin_client):
    with Session(engine) as session:
        session.add(ScanRun(trigger="manual", scope="full", status=ScanStatus.completed))
        session.add(ScanRun(trigger="realtime", scope="torrents", status=ScanStatus.running))
        session.commit()

    status = admin_client.get("/api/scan/status").json()

    assert (status["trigger"], status["status"]) == ("manual", "completed")


@pytest.mark.parametrize(
    "scope, slow_step",
    [
        ("torrents", "app.services.torrent_match._resolve_files"),
        ("torrents", "app.services.partial_scan.attach_torrents"),
        ("queue", "app.services.partial_scan.refresh_statuses"),
    ],
)
def test_an_analysis_never_freezes_the_application(
    fake_http, fake_inodes, settings, tmp_path, monkeypatch, scope, slow_step
):
    """Inodes des torrents, rattachement et statuts lisent le disque (lent sur
    un partage Unraid) : faits dans la boucle asyncio, ils figeaient toute
    l'interface pendant chaque analyse du temps réel — une requête de 30 ms en
    prenait 18 s, puis encore 6 s le temps de lire les inodes des torrents."""
    seed_library(tmp_path)
    fake_http["http://qbit"] = qbit_handler(
        [{"hash": "aaa", "name": "Matrix", "save_path": "/data/torrents", "size": 10}]
    )
    module_name, _, name = slow_step.rpartition(".")
    module = importlib.import_module(module_name)
    real = getattr(module, name)

    def slow(*args, **kwargs):
        time.sleep(0.3)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, slow)

    async def run() -> int:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        await run_service_scan(scope)
        task.cancel()
        return ticks

    assert asyncio.run(run()) >= 10
