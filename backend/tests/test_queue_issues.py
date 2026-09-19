"""Imports bloqués : détection dans la file d'attente Sonarr/Radarr, effet sur
les statuts, et relance d'import."""

import asyncio

import httpx
import pytest

from app.clients.arr import RadarrClient, SonarrClient
from app.models.media import ImportIssue, MediaFile, Torrent
from app.services.queue_issues import index_queue_issues, issue_rows_for, retry_import
from app.services.scan import compute_statuses

KEY = "arr-key"

BLOCKED = {
    "id": 12,
    "movieId": 7,
    "seriesId": 7,
    "downloadId": "ABCDEF0123456789",
    "title": "Movie.2024.1080p-GROUP",
    "size": 8_000_000_000,
    "trackedDownloadState": "importBlocked",
    "trackedDownloadStatus": "warning",
    "statusMessages": [{"title": "Movie.2024", "messages": ["Not a preferred word upgrade for existing movie file"]}],
}
DOWNLOADING = {
    "id": 13,
    "movieId": 8,
    "downloadId": "FEDCBA9876543210",
    "title": "Other.2024-GROUP",
    "trackedDownloadState": "downloading",
    "trackedDownloadStatus": "ok",
}
PENDING_OK = {**DOWNLOADING, "id": 14, "movieId": 9, "trackedDownloadState": "importPending"}
PENDING_WARNING = {**PENDING_OK, "id": 15, "movieId": 10, "trackedDownloadStatus": "warning"}


def test_only_blocked_records_are_kept():
    issues = index_queue_issues([BLOCKED, DOWNLOADING, PENDING_OK, PENDING_WARNING], "movieId")
    # Un téléchargement en cours, ou un import en attente sans avertissement,
    # est le fonctionnement normal : rien à signaler.
    assert sorted(issues) == [7, 10]


def test_rows_carry_reason_and_download_id():
    row = issue_rows_for([BLOCKED])[0]
    assert row.download_id == "ABCDEF0123456789"
    assert "preferred word" in row.reason
    assert row.state == "importBlocked"
    assert row.size == 8_000_000_000


def test_series_rows_carry_the_episode_label():
    record = {**BLOCKED, "episode": {"seasonNumber": 3, "episodeNumber": 7}}
    assert issue_rows_for([record])[0].episode_label == "S03E07"


def test_reason_falls_back_to_the_error_message():
    record = {**BLOCKED, "statusMessages": [], "errorMessage": "Import failed"}
    assert issue_rows_for([record])[0].reason == "Import failed"


# --- Statuts ------------------------------------------------------------------


def stuck_torrent() -> Torrent:
    return Torrent(
        media_id=0,
        hash="abcdef0123456789",
        name="Movie.2024.1080p-GROUP",
        save_path="/data/downloads",
        content_path="/data/downloads/Movie.2024.1080p-GROUP",
        size=8_000_000_000,
        trackers_json="[]",
        is_hardlinked=False,
        repairable=False,
    )


def test_blocked_import_replaces_missing_media_server():
    statuses, _ = compute_statuses([], [], has_emby_item=False, queue_kinds={"import"})
    # Le média n'est pas « absent du serveur multimédia » : il est bloqué avant.
    assert "import_rate" in statuses
    assert "manquant_emby" not in statuses
    assert "manquant_qbit" not in statuses


def test_torrent_waiting_for_import_is_not_an_orphan():
    torrent = stuck_torrent()
    statuses, reclaimable = compute_statuses(
        [], [torrent], has_emby_item=False, import_blocked_hashes={torrent.hash}, queue_kinds={"import"}
    )
    assert "orphelin_qbit" not in statuses
    assert reclaimable == 0


def test_a_real_orphan_is_still_reported_alongside_a_blocked_import():
    stuck, orphan = stuck_torrent(), stuck_torrent()
    orphan.hash = "0000000000000000"
    statuses, reclaimable = compute_statuses(
        [], [stuck, orphan], has_emby_item=True, import_blocked_hashes={stuck.hash}, queue_kinds={"import"}
    )
    assert {"import_rate", "orphelin_qbit"} <= statuses
    assert reclaimable == orphan.size


def test_media_server_absence_is_still_reported_when_files_exist():
    library_file = MediaFile(media_id=0, path="/data/media/Movie.mkv", size=1, episode_label=None, is_current=True)
    statuses, _ = compute_statuses([library_file], [], has_emby_item=False, queue_kinds={"import"})
    assert {"import_rate", "manquant_emby"} <= statuses


# --- Relance de l'import ------------------------------------------------------


def arr_server(calls: list[httpx.Request], candidates: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/v3/manualimport":
            return httpx.Response(200, json=candidates)
        if request.url.path == "/api/v3/command":
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)

    return handler


MOVIE_CANDIDATE = {
    "path": "/downloads/Movie.2024.1080p-GROUP/movie.mkv",
    "downloadId": "ABCDEF0123456789",
    "movie": {"id": 7},
    "quality": {"quality": {"id": 7}},
    "languages": [{"id": 1}],
    "releaseGroup": "GROUP",
    "rejections": [],
}


def test_retry_sends_only_the_files_of_that_download(fake_http):
    calls: list[httpx.Request] = []
    fake_http["http://radarr"] = arr_server(calls, [MOVIE_CANDIDATE])

    imported, rejections = asyncio.run(
        retry_import(RadarrClient("http://radarr", KEY), "ABCDEF0123456789", is_series=False)
    )

    assert (imported, rejections) == (1, [])
    assert calls[0].url.params.get("downloadId") == "ABCDEF0123456789"
    command = calls[1].read().decode()
    assert '"ManualImport"' in command and '"movieId":7' in command


def test_permanently_rejected_files_are_never_imported(fake_http):
    calls: list[httpx.Request] = []
    rejected = {**MOVIE_CANDIDATE, "rejections": [{"reason": "Sample", "type": "permanent"}]}
    fake_http["http://radarr"] = arr_server(calls, [rejected])

    imported, rejections = asyncio.run(
        retry_import(RadarrClient("http://radarr", KEY), "ABCDEF0123456789", is_series=False)
    )

    assert imported == 0
    assert rejections == ["Sample"]
    # Aucune commande envoyée : rien à importer.
    assert all(call.url.path != "/api/v3/command" for call in calls)


def test_temporary_rejections_do_not_block_the_retry(fake_http):
    calls: list[httpx.Request] = []
    candidate = {**MOVIE_CANDIDATE, "rejections": [{"reason": "File is locked", "type": "temporary"}]}
    fake_http["http://radarr"] = arr_server(calls, [candidate])

    imported, _ = asyncio.run(retry_import(RadarrClient("http://radarr", KEY), "ABCDEF0123456789", is_series=False))
    assert imported == 1


def test_a_file_tied_to_no_media_is_never_imported(fake_http):
    calls: list[httpx.Request] = []
    orphan_candidate = {k: v for k, v in MOVIE_CANDIDATE.items() if k != "movie"}
    fake_http["http://radarr"] = arr_server(calls, [orphan_candidate])

    imported, rejections = asyncio.run(
        retry_import(RadarrClient("http://radarr", KEY), "ABCDEF0123456789", is_series=False)
    )
    assert imported == 0 and rejections
    assert all(call.url.path != "/api/v3/command" for call in calls)


def test_series_retry_carries_the_episode_ids(fake_http):
    calls: list[httpx.Request] = []
    candidate = {
        "path": "/downloads/Show.S03E07/episode.mkv",
        "downloadId": "ABCDEF0123456789",
        "series": {"id": 4},
        "episodes": [{"id": 41}, {"id": 42}],
        "seasonNumber": 3,
        "rejections": [],
    }
    fake_http["http://sonarr"] = arr_server(calls, [candidate])

    imported, _ = asyncio.run(retry_import(SonarrClient("http://sonarr", KEY), "ABCDEF0123456789", is_series=True))

    assert imported == 1
    body = calls[1].read().decode()
    assert '"seriesId":4' in body and '"episodeIds":[41,42]' in body


@pytest.mark.parametrize("status", [401, 500])
def test_an_unreachable_server_never_raises(fake_http, status):
    fake_http["http://radarr"] = lambda request: httpx.Response(status)
    from app.services.queue_issues import safe_retry_import

    imported, rejections, error = asyncio.run(
        safe_retry_import(RadarrClient("http://radarr", KEY), "ABCDEF0123456789", is_series=False)
    )
    assert imported == 0 and rejections == [] and error is not None


def test_issue_rows_are_persisted_per_media(fresh_database):
    from sqlmodel import Session, select

    from app.database import engine

    with Session(engine) as session:
        row = issue_rows_for([BLOCKED])[0]
        row.media_id = 1
        session.add(row)
        session.commit()
        stored = session.exec(select(ImportIssue)).all()
        assert len(stored) == 1 and stored[0].media_id == 1


# --- Téléchargements en souffrance -------------------------------------------

STALLED = {
    "id": 20,
    "movieId": 11,
    "downloadId": "1111111111111111",
    "title": "Movie.2025-GROUP",
    "trackedDownloadState": "downloading",
    "trackedDownloadStatus": "warning",
    "status": "warning",
    "statusMessages": [{"title": "Movie.2025", "messages": ["The download is stalled with no connections"]}],
}


def test_a_stalled_download_is_kept_and_typed():
    issues = index_queue_issues([STALLED, DOWNLOADING], "movieId")
    assert sorted(issues) == [11]
    assert issue_rows_for(issues[11])[0].kind == "stalled"


def test_a_blocked_import_stays_an_import_not_a_stall():
    assert issue_rows_for([BLOCKED])[0].kind == "import"


def test_stalled_download_is_neither_missing_nor_orphan():
    torrent = stuck_torrent()
    statuses, reclaimable = compute_statuses(
        [],
        [torrent],
        has_emby_item=False,
        import_blocked_hashes={torrent.hash},
        queue_kinds={"stalled"},
    )
    assert statuses == {"telechargement_bloque"}
    assert reclaimable == 0


def test_stalled_download_never_offers_a_retry(fresh_database):
    """La relance ne concerne que les imports : un téléchargement qui n'est pas
    arrivé n'a rien à importer."""
    from sqlmodel import Session

    from app.database import engine
    from app.models.media import Media, MediaType
    from app.models.settings import Settings
    from app.services.queue_issues import execute_import_retry

    with Session(engine) as session:
        media = Media(media_type=MediaType.movie, title="Movie", radarr_id=11)
        session.add(media)
        session.commit()
        session.refresh(media)
        row = issue_rows_for([STALLED])[0]
        row.media_id = media.id
        session.add(row)
        session.commit()

        steps, imported = asyncio.run(execute_import_retry(session, media, Settings(id=1)))
        assert steps == [] and imported == 0


# --- Relance : replis successifs ----------------------------------------------


def arr_server_with_fallback(calls: list[httpx.Request], by_download: list[dict], by_folder: list[dict]):
    """Sonarr/Radarr ne reconnaît plus le downloadId une fois l'entrée sortie de
    la file, mais retrouve les fichiers par dossier de sortie."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/v3/manualimport":
            wanted = by_folder if request.url.params.get("folder") else by_download
            return httpx.Response(200, json=wanted)
        if request.url.path == "/api/v3/command":
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)

    return handler


def test_output_path_is_kept_from_the_queue():
    record = {**BLOCKED, "outputPath": "/data/downloads/Movie.2024.1080p-GROUP"}
    assert issue_rows_for([record])[0].output_path == "/data/downloads/Movie.2024.1080p-GROUP"


def test_retry_falls_back_to_the_output_folder(fake_http):
    calls: list[httpx.Request] = []
    fake_http["http://radarr"] = arr_server_with_fallback(calls, [], [MOVIE_CANDIDATE])

    imported, rejections = asyncio.run(
        retry_import(
            RadarrClient("http://radarr", KEY),
            "ABCDEF0123456789",
            is_series=False,
            output_path="/data/downloads/Movie.2024.1080p-GROUP",
        )
    )

    assert (imported, rejections) == (1, [])
    assert calls[0].url.params.get("downloadId") == "ABCDEF0123456789"
    assert calls[1].url.params.get("folder") == "/data/downloads/Movie.2024.1080p-GROUP"


def test_without_any_candidate_the_queue_processing_is_relaunched(fake_http):
    calls: list[httpx.Request] = []
    fake_http["http://radarr"] = arr_server_with_fallback(calls, [], [])

    imported, rejections = asyncio.run(
        retry_import(RadarrClient("http://radarr", KEY), "ABCDEF0123456789", is_series=False, output_path="/data/x")
    )

    assert imported == 0 and rejections
    command = calls[-1]
    assert command.url.path == "/api/v3/command" and "ProcessMonitoredDownloads" in command.read().decode()


def test_empty_fields_are_never_sent_in_the_command(fake_http):
    calls: list[httpx.Request] = []
    bare = {"path": "/downloads/movie.mkv", "movie": {"id": 7}, "quality": None, "languages": [], "rejections": []}
    fake_http["http://radarr"] = arr_server_with_fallback(calls, [bare], [])

    asyncio.run(retry_import(RadarrClient("http://radarr", KEY), "ABCDEF0123456789", is_series=False))

    body = calls[-1].read().decode()
    # Sonarr/Radarr refuse une qualité nulle : le champ ne doit pas être envoyé.
    assert '"quality"' not in body and '"languages"' not in body
    assert '"movieId":7' in body


def test_the_server_explanation_is_reported(fake_http):
    from app.services.queue_issues import safe_retry_import

    fake_http["http://radarr"] = lambda request: httpx.Response(400, text="Quality is required")

    imported, _rejections, error = asyncio.run(
        safe_retry_import(RadarrClient("http://radarr", KEY), "ABCDEF0123456789", is_series=False)
    )
    assert imported == 0
    assert error is not None and "400" in error and "Quality is required" in error


# --- Rattachement : jamais le mauvais média -----------------------------------


def test_each_media_only_gets_its_own_records():
    other = {**BLOCKED, "id": 30, "movieId": 99, "title": "Autre.2024-GROUP", "downloadId": "9999"}
    issues = index_queue_issues([BLOCKED, other], "movieId")
    assert [r["title"] for r in issues[7]] == ["Movie.2024.1080p-GROUP"]
    assert [r["title"] for r in issues[99]] == ["Autre.2024-GROUP"]


def test_a_record_without_media_id_is_dropped():
    unknown = {k: v for k, v in BLOCKED.items() if k not in ("movieId", "seriesId")}
    assert index_queue_issues([unknown], "movieId") == {}


@pytest.mark.parametrize("value", [0, -1, True, "7", 7.0, None])
def test_only_a_real_positive_id_attaches_a_record(value):
    assert index_queue_issues([{**BLOCKED, "movieId": value}], "movieId") == {}


def test_a_series_record_is_never_read_as_a_movie_one():
    series_record = {**BLOCKED}
    series_record.pop("movieId")
    assert index_queue_issues([series_record], "movieId") == {}
    assert sorted(index_queue_issues([series_record], "seriesId")) == [7]
