"""Imports bloqués : détection dans la file d'attente Sonarr/Radarr, effet sur
les statuts, et relance d'import."""

import asyncio
import json

import httpx
import pytest
from sqlmodel import Session

from app.clients.arr import RadarrClient, SonarrClient
from app.database import engine
from app.models.media import ImportIssue, Media, MediaFile, MediaType, Torrent
from app.models.settings import Settings
from app.services import queue_issues
from app.services.queue_issues import (
    execute_import_retry,
    index_queue_issues,
    issue_rows_for,
    prepare_import,
    triage_candidates,
)
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


# --- Relance de l'import : tri des fichiers proposés ---------------------------

GB = 1024**3

MOVIE_CANDIDATE = {
    "path": "/downloads/Movie.2024.1080p-GROUP/movie.mkv",
    "downloadId": "ABCDEF0123456789",
    "size": 8 * GB,
    "movie": {"id": 7},
    "quality": {"quality": {"id": 7}},
    "languages": [{"id": 1}],
    "releaseGroup": "GROUP",
    "rejections": [],
}
EPISODE_CANDIDATE = {
    "path": "/downloads/South.Park.S29E03/south.park.s29e03.mkv",
    "downloadId": "ABCDEF0123456789",
    "size": GB,
    "series": {"id": 4},
    "episodes": [{"id": 41}],
    "seasonNumber": 29,
    "releaseType": "singleEpisode",
    "quality": {"quality": {"id": 3}},
    "languages": [{"id": 1}],
    "rejections": [],
}


def rejected(candidate: dict, *reasons: str, kind: str = "permanent") -> dict:
    return {**candidate, "rejections": [{"reason": reason, "type": kind} for reason in reasons]}


def test_harmless_refusals_are_lifted_like_the_arr_manual_import():
    """Cas réel : South Park S29E03 bloqué par un titre d'épisode encore « TBA »
    et une durée illisible — deux refus que l'import manuel de Sonarr lève."""
    candidate = rejected(
        EPISODE_CANDIDATE, "Episode has a TBA title and recently aired", "Unable to determine if file is a sample"
    )
    attempt = triage_candidates([candidate], is_series=True)
    assert len(attempt.files) == 1 and attempt.rejections == []
    assert attempt.overridden == [
        "Episode has a TBA title and recently aired",
        "Unable to determine if file is a sample",
    ]


@pytest.mark.parametrize(
    "reason",
    [
        "Not an upgrade for existing episode file(s). Existing quality: WEBDL-1080p. New Quality WEBDL-720p.",
        "Not a Custom Format upgrade for existing episode file(s)",
        "Sample",
        "Episode 3x04 was unexpected considering the South.Park.S29E03 folder name",
    ],
)
def test_protective_refusals_stay_blocking(reason):
    # Remplacer un meilleur fichier, importer un échantillon avéré ou un épisode
    # douteux : à trancher dans Sonarr/Radarr, jamais d'office.
    attempt = triage_candidates([rejected(EPISODE_CANDIDATE, reason)], is_series=True)
    assert attempt.files == [] and attempt.rejections == [reason]


def test_a_small_file_of_unknown_duration_beside_the_main_one_is_a_sample():
    unknown = "Unable to determine if file is a sample"
    main = rejected({**MOVIE_CANDIDATE, "path": "/d/movie.mkv"}, unknown)
    small = rejected({**MOVIE_CANDIDATE, "path": "/d/sample.mkv", "size": 50 * 1024**2}, unknown)
    attempt = triage_candidates([main, small], is_series=False)
    assert [file["path"] for file in attempt.files] == ["/d/movie.mkv"]
    assert attempt.rejections == [unknown]


def test_temporary_refusals_do_not_block():
    attempt = triage_candidates([rejected(MOVIE_CANDIDATE, "Locked file", kind="temporary")], is_series=False)
    assert len(attempt.files) == 1


def test_a_file_tied_to_no_media_is_never_imported():
    orphan_candidate = {k: v for k, v in MOVIE_CANDIDATE.items() if k != "movie"}
    attempt = triage_candidates([orphan_candidate], is_series=False)
    assert attempt.files == [] and attempt.rejections


def test_the_command_carries_what_the_arr_import_needs():
    episode = triage_candidates([EPISODE_CANDIDATE], is_series=True).files[0]
    assert episode["seriesId"] == 4 and episode["episodeIds"] == [41] and episode["seasonNumber"] == 29
    assert episode["releaseType"] == "singleEpisode"
    # Sonarr/Radarr refuse une qualité nulle : un champ vide n'est jamais envoyé.
    bare = {"path": "/downloads/movie.mkv", "movie": {"id": 7}, "quality": None, "languages": [], "rejections": []}
    assert triage_candidates([bare], is_series=False).files == [{"path": "/downloads/movie.mkv", "movieId": 7}]


# --- Relance de l'import : ce que l'on demande à Sonarr/Radarr ----------------


class FakeArr:
    """Sonarr/Radarr réduit à ce que la relance utilise : file d'attente,
    fichiers proposés (par téléchargement ou par dossier), commandes."""

    def __init__(self, queue: list[dict], by_download: list[dict], by_folder: list[dict] | None = None):
        self.queue = queue
        self.by_download = by_download
        self.by_folder = by_folder or []
        self.calls: list[httpx.Request] = []
        self.commands: list[dict] = []
        self.command_status = "completed"
        # Ce que l'import laisse dans la file : par défaut, plus rien.
        self.queue_after: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path == "/api/v3/queue":
            return httpx.Response(200, json={"records": self.queue, "totalRecords": len(self.queue)})
        if path == "/api/v3/manualimport":
            return httpx.Response(200, json=self.by_folder if request.url.params.get("folder") else self.by_download)
        if path == "/api/v3/command" and request.method == "POST":
            self.commands.append(json.loads(request.read()))
            if self.commands[-1]["name"] == "ManualImport" and self.command_status == "completed":
                self.queue = self.queue_after
            return httpx.Response(201, json={"id": len(self.commands), "status": "queued"})
        if path.startswith("/api/v3/command/"):
            return httpx.Response(200, json={"id": 1, "status": self.command_status})
        return httpx.Response(404)

    def requests(self, path: str) -> list[httpx.Request]:
        return [call for call in self.calls if call.url.path == path]


def test_radarr_is_told_which_movie_the_download_belongs_to(fake_http):
    radarr = FakeArr([], [MOVIE_CANDIDATE])
    fake_http["http://radarr"] = radarr
    asyncio.run(prepare_import(RadarrClient("http://radarr", KEY), "ABCDEF0123456789", False, None, 7))
    params = radarr.requests("/api/v3/manualimport")[0].url.params
    assert params["downloadId"] == "ABCDEF0123456789" and params["movieId"] == "7"


def test_sonarr_is_never_given_a_series_id(fake_http):
    # Chez Sonarr, `seriesId` liste les fichiers DÉJÀ dans la série, pas ceux du
    # téléchargement (ManualImportController.GetMediaFiles).
    sonarr = FakeArr([], [EPISODE_CANDIDATE])
    fake_http["http://sonarr"] = sonarr
    asyncio.run(prepare_import(SonarrClient("http://sonarr", KEY), "ABCDEF0123456789", True, None, 4))
    assert "seriesId" not in sonarr.requests("/api/v3/manualimport")[0].url.params


def test_the_output_folder_is_the_fallback(fake_http):
    radarr = FakeArr([], [], [MOVIE_CANDIDATE])
    fake_http["http://radarr"] = radarr
    attempt = asyncio.run(
        prepare_import(RadarrClient("http://radarr", KEY), "ABCDEF0123456789", False, "/data/downloads/Movie", 7)
    )
    assert len(attempt.files) == 1
    folder = radarr.requests("/api/v3/manualimport")[1].url.params
    # Sans téléchargement, un identifiant de film ferait lister la bibliothèque.
    assert folder["folder"] == "/data/downloads/Movie" and "movieId" not in folder


def test_without_any_file_the_queue_processing_is_relaunched(fake_http):
    radarr = FakeArr([], [], [])
    fake_http["http://radarr"] = radarr
    attempt = asyncio.run(prepare_import(RadarrClient("http://radarr", KEY), "ABCDEF0123456789", False, "/x", 7))
    assert attempt.relaunched and attempt.files == []
    assert radarr.commands == [{"name": "ProcessMonitoredDownloads"}]


# --- Relance de l'import : de bout en bout ------------------------------------


@pytest.fixture
def quick_commands(monkeypatch):
    monkeypatch.setattr(queue_issues, "COMMAND_POLL_SECONDS", 0)


@pytest.fixture
def notified(monkeypatch) -> list[int]:
    seen: list[int] = []
    monkeypatch.setattr(queue_issues.hub, "media_changed", lambda media_id, **_: seen.append(media_id))
    return seen


def blocked_media(media_type: MediaType = MediaType.movie, arr_id: int = 7):
    """Média dont le dernier scan a relevé un import bloqué."""
    session = Session(engine)
    is_series = media_type == MediaType.series
    media = Media(
        media_type=media_type,
        title="South Park" if is_series else "Movie",
        sonarr_id=arr_id if is_series else None,
        radarr_id=None if is_series else arr_id,
    )
    session.add(media)
    session.commit()
    session.refresh(media)
    row = issue_rows_for([BLOCKED])[0]
    row.media_id = media.id
    session.add(row)
    session.commit()
    settings = Settings(
        id=1, radarr_url="http://radarr", radarr_api_key=KEY, sonarr_url="http://sonarr", sonarr_api_key=KEY
    )
    return session, media, settings


def test_a_blocked_episode_is_imported_and_checked(fresh_database, fake_http, quick_commands, notified):
    record = {
        **BLOCKED,
        "seriesId": 4,
        "title": "South Park S29E03 1080p WEB H264-MeGusta",
        "statusMessages": [{"title": "x", "messages": ["Episode has a TBA title and recently aired"]}],
    }
    candidate = rejected(
        EPISODE_CANDIDATE, "Episode has a TBA title and recently aired", "Unable to determine if file is a sample"
    )
    sonarr = FakeArr([record], [candidate])
    fake_http["http://sonarr"] = sonarr
    session, media, settings = blocked_media(MediaType.series, arr_id=4)

    steps, imported = asyncio.run(execute_import_retry(session, media, settings))

    assert imported == 1
    assert [step.success for step in steps] == [True]
    assert "1 fichier(s) importé(s)" in steps[0].label and "TBA title" in steps[0].label
    command = sonarr.commands[-1]
    assert command["name"] == "ManualImport" and command["importMode"] == "auto"
    assert command["files"][0]["episodeIds"] == [41]
    # La file est lue en direct, filtrée sur la série, avant ET après l'import.
    queue_reads = sonarr.requests("/api/v3/queue")
    assert len(queue_reads) == 2 and queue_reads[0].url.params["seriesIds"] == "4"
    assert notified == [media.id]


def test_an_import_already_done_is_not_retried(fresh_database, fake_http, quick_commands, notified):
    radarr = FakeArr([], [MOVIE_CANDIDATE])
    fake_http["http://radarr"] = radarr
    session, media, settings = blocked_media()

    steps, imported = asyncio.run(execute_import_retry(session, media, settings))

    assert imported == 0 and [step.success for step in steps] == [True]
    assert "plus aucun import bloqué" in steps[0].label
    assert radarr.requests("/api/v3/manualimport") == [] and radarr.commands == []
    assert notified == [media.id]


def test_an_import_still_blocked_afterwards_is_reported(fresh_database, fake_http, quick_commands, notified):
    radarr = FakeArr([BLOCKED], [MOVIE_CANDIDATE])
    radarr.queue_after = [BLOCKED]
    fake_http["http://radarr"] = radarr
    session, media, settings = blocked_media()

    steps, imported = asyncio.run(execute_import_retry(session, media, settings))

    assert imported == 0 and not steps[0].success
    assert "n'a pas pu tout importer" in (steps[0].error or "") and "preferred word" in (steps[0].error or "")
    assert notified == []


def test_an_import_still_running_is_announced_as_such(fresh_database, fake_http, quick_commands, notified, monkeypatch):
    monkeypatch.setattr(queue_issues, "COMMAND_TIMEOUT_SECONDS", 0)
    radarr = FakeArr([BLOCKED], [MOVIE_CANDIDATE])
    radarr.command_status = "started"
    fake_http["http://radarr"] = radarr
    session, media, settings = blocked_media()

    steps, imported = asyncio.run(execute_import_retry(session, media, settings))

    assert imported == 1 and steps[0].success and "en cours" in steps[0].label


def test_a_failed_command_is_reported(fresh_database, fake_http, quick_commands, notified):
    radarr = FakeArr([BLOCKED], [MOVIE_CANDIDATE])
    radarr.command_status = "failed"
    fake_http["http://radarr"] = radarr
    session, media, settings = blocked_media()

    steps, imported = asyncio.run(execute_import_retry(session, media, settings))

    assert imported == 0 and not steps[0].success and "failed" in (steps[0].error or "")


def test_a_protective_refusal_sends_nothing(fresh_database, fake_http, quick_commands, notified):
    radarr = FakeArr([BLOCKED], [rejected(MOVIE_CANDIDATE, "Sample")])
    fake_http["http://radarr"] = radarr
    session, media, settings = blocked_media()

    steps, _ = asyncio.run(execute_import_retry(session, media, settings))

    assert not steps[0].success and "import manuel" in (steps[0].error or "") and "Sample" in (steps[0].error or "")
    assert radarr.commands == []


def test_only_the_downloads_of_this_media_are_retried(fresh_database, fake_http, quick_commands, notified):
    other = {**BLOCKED, "id": 30, "movieId": 99, "downloadId": "9999"}
    # Un Radarr trop ancien ignore le filtre `movieIds` : la file est refiltrée.
    radarr = FakeArr([other, BLOCKED], [MOVIE_CANDIDATE])
    radarr.queue_after = [other]
    fake_http["http://radarr"] = radarr
    session, media, settings = blocked_media()

    steps, _ = asyncio.run(execute_import_retry(session, media, settings))

    assert len(steps) == 1 and steps[0].success
    assert [call.url.params["downloadId"] for call in radarr.requests("/api/v3/manualimport")] == ["ABCDEF0123456789"]


@pytest.mark.parametrize("status", [401, 500])
def test_an_unreachable_server_never_raises(fresh_database, fake_http, status, notified):
    fake_http["http://radarr"] = lambda request: httpx.Response(status)
    session, media, settings = blocked_media()

    steps, imported = asyncio.run(execute_import_retry(session, media, settings))

    assert imported == 0 and not steps[0].success and str(status) in (steps[0].error or "")


def test_the_server_explanation_is_reported(fresh_database, fake_http, quick_commands, notified):
    radarr = FakeArr([BLOCKED], [MOVIE_CANDIDATE])

    def refuse_command(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/command":
            return httpx.Response(400, text="Quality is required")
        return radarr(request)

    fake_http["http://radarr"] = refuse_command
    session, media, settings = blocked_media()

    steps, _ = asyncio.run(execute_import_retry(session, media, settings))

    assert "400" in (steps[0].error or "") and "Quality is required" in (steps[0].error or "")


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


def test_output_path_is_kept_from_the_queue():
    record = {**BLOCKED, "outputPath": "/data/downloads/Movie.2024.1080p-GROUP"}
    assert issue_rows_for([record])[0].output_path == "/data/downloads/Movie.2024.1080p-GROUP"


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
