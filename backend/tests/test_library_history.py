"""Historique de la bibliothèque (services/library_history.py) : une
photographie par jour, jamais de chiffre faux (bibliothèque pas encore
analysée, disque non monté), rétention, et la route de lecture."""

import asyncio
import logging
import time
from datetime import date, timedelta

import pytest
from pydantic import TypeAdapter
from sqlmodel import Session, select

from app.database import engine
from app.models.library_snapshot import LibrarySnapshot
from app.models.media import Media, MediaType, ScanRun, ScanStatus
from app.services import library_history
from app.services.library_history import (
    DiskUsage,
    disks_of,
    history,
    record_missing_snapshot,
    record_snapshot,
    take_snapshot,
    today,
)
from app.services.partial_scan import run_service_scan
from app.services.scan import run_scan
from app.services.scheduler import configure_library_snapshots, scheduler
from tests import test_scan_characterization as world
from tests.test_partial_scan import qbit_handler, seed_library

DAY = date(2026, 9, 29)


def _full_scan_done(session: Session) -> None:
    session.add(ScanRun(status=ScanStatus.completed, scope="full"))
    session.commit()


def _media(session: Session, media_type: MediaType, size: int, title: str = "Titre") -> None:
    session.add(Media(media_type=media_type, title=title, total_size=size))
    session.commit()


def _rows(session: Session) -> list[LibrarySnapshot]:
    session.expire_all()
    return list(session.exec(select(LibrarySnapshot).order_by(LibrarySnapshot.day)).all())


# --- Photographie ---------------------------------------------------------------


def test_nothing_is_recorded_before_a_full_scan(session, settings):
    """Première installation, ou cache recréé après une mise à jour : la
    bibliothèque paraîtrait vide, puis bondirait au scan suivant."""
    _media(session, MediaType.movie, 100)
    session.add(ScanRun(status=ScanStatus.completed, scope="torrents"))
    session.add(ScanRun(status=ScanStatus.failed, scope="full"))
    session.commit()

    assert take_snapshot(session, settings, DAY) is False
    assert _rows(session) == []


def test_totals_by_type_and_both_disks(session, settings):
    _full_scan_done(session)
    _media(session, MediaType.movie, 100)
    _media(session, MediaType.movie, 200)
    _media(session, MediaType.series, 50)

    assert take_snapshot(session, settings, DAY) is True

    [row] = _rows(session)
    assert (row.day, row.media_count, row.movie_count, row.series_count) == (DAY, 3, 2, 1)
    assert (row.total_size, row.movie_size, row.series_size) == (350, 300, 50)
    library, downloads = disks_of(row)
    assert (library.role, library.path, downloads.role, downloads.path) == (
        "library",
        settings.emby_library_path,
        "downloads",
        settings.qbittorrent_download_path,
    )
    for disk in (library, downloads):
        assert disk.available and disk.total and disk.free is not None and disk.device is not None


def test_the_last_measure_of_the_day_wins(session, settings):
    _full_scan_done(session)
    _media(session, MediaType.movie, 100)
    take_snapshot(session, settings, DAY)
    _media(session, MediaType.series, 40)

    take_snapshot(session, settings, DAY)

    [row] = _rows(session)
    assert (row.media_count, row.total_size) == (2, 140)


def test_retention_keeps_exactly_the_configured_days(session, settings):
    settings.library_history_retention_days = 30
    session.add(settings)
    for age in range(40, 0, -1):
        session.add(LibrarySnapshot(day=DAY - timedelta(days=age)))
    _full_scan_done(session)

    take_snapshot(session, settings, DAY)

    days = [row.day for row in _rows(session)]
    assert len(days) == 30
    assert days[0] == DAY - timedelta(days=29) and days[-1] == DAY


def test_retention_outside_bounds_is_clamped(session, settings):
    settings.library_history_retention_days = 1
    assert library_history.retention_days(settings) == library_history.MIN_RETENTION_DAYS
    settings.library_history_retention_days = 100_000
    assert library_history.retention_days(settings) == library_history.MAX_RETENTION_DAYS
    assert library_history.retention_days(None) == library_history.DEFAULT_RETENTION_DAYS


def test_an_unmounted_root_is_recorded_without_figures(session, settings, tmp_path):
    """Sans ce garde-fou, `disk_usage` mesurerait le disque système du
    conteneur et fausserait toutes les prévisions."""
    empty_mount_point = tmp_path / "media"
    empty_mount_point.mkdir()
    settings.emby_library_path = str(empty_mount_point)
    settings.qbittorrent_download_path = str(tmp_path / "absent")
    session.add(settings)
    _full_scan_done(session)

    take_snapshot(session, settings, DAY)

    [row] = _rows(session)
    for disk in disks_of(row):
        assert (disk.available, disk.total, disk.free, disk.device) == (False, None, None, None)


def test_an_unreadable_disk_is_recorded_as_unavailable(session, settings, monkeypatch, caplog):
    def refuse(_path):
        raise PermissionError("refusé")

    monkeypatch.setattr(library_history.shutil, "disk_usage", refuse)
    _full_scan_done(session)

    with caplog.at_level(logging.WARNING, logger="app.services.library_history"):
        take_snapshot(session, settings, DAY)

    [row] = _rows(session)
    assert [disk.available for disk in disks_of(row)] == [False, False]
    assert "PermissionError" in caplog.text


def test_a_root_left_empty_in_settings_is_not_measured(session, settings):
    settings.qbittorrent_download_path = "  "
    _full_scan_done(session)

    take_snapshot(session, settings, DAY)

    [row] = _rows(session)
    assert [disk.role for disk in disks_of(row)] == ["library"]


def test_unreadable_disk_json_reads_as_no_disk(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.library_history"):
        assert disks_of(LibrarySnapshot(day=DAY, disks="pas du json")) == []
    assert "illisible" in caplog.text


def test_history_returns_the_requested_days_oldest_first(session):
    for age in (0, 1, 5, 10):
        session.add(LibrarySnapshot(day=DAY - timedelta(days=age)))
    session.commit()

    assert [row.day for row in history(session, 6, DAY)] == [DAY - timedelta(days=5), DAY - timedelta(days=1), DAY]


# --- Tâches de fond -------------------------------------------------------------


def test_a_background_failure_is_logged_never_raised(session, settings, monkeypatch, caplog):
    def broken(_session):
        raise RuntimeError("base indisponible")

    monkeypatch.setattr(library_history, "library_totals", broken)
    _full_scan_done(session)

    with caplog.at_level(logging.WARNING, logger="app.services.library_history"):
        record_snapshot()
        record_missing_snapshot()

    assert caplog.text.count("impossible") == 2
    assert _rows(session) == []


def test_startup_does_not_redo_the_measure_of_the_day(session, settings):
    _full_scan_done(session)
    _media(session, MediaType.movie, 100)
    record_missing_snapshot()
    _media(session, MediaType.movie, 900)

    record_missing_snapshot()

    [row] = _rows(session)
    assert (row.day, row.total_size) == (today(), 100)


def test_startup_fills_a_missing_day(session, settings):
    _full_scan_done(session)
    session.add(LibrarySnapshot(day=today() - timedelta(days=1)))
    session.commit()

    record_missing_snapshot()

    assert [row.day for row in _rows(session)][-1] == today()


def test_jobs_are_planned_once():
    try:
        configure_library_snapshots()
        configure_library_snapshots()
        daily = scheduler.get_job("library_snapshot")
        startup = scheduler.get_job("library_snapshot_startup")
        assert daily is not None and startup is not None
        assert len([job for job in scheduler.get_jobs() if job.id.startswith("library_snapshot")]) == 2
        # Fonctions synchrones : APScheduler les exécute dans un thread, jamais
        # dans la boucle asyncio.
        assert not asyncio.iscoroutinefunction(daily.func)
        assert not asyncio.iscoroutinefunction(startup.func)
    finally:
        for job_id in ("library_snapshot", "library_snapshot_startup"):
            if scheduler.get_job(job_id) is not None:
                scheduler.remove_job(job_id)


def test_a_full_scan_records_the_day(fake_http, portable_inodes, session, settings, tmp_path):
    w = world.build_world(tmp_path)
    settings.emby_library_path = w["media"]
    settings.qbittorrent_download_path = w["torrents"]
    session.add(settings)
    session.commit()
    fake_http["http://emby"] = world.emby_handler(w)
    fake_http["http://radarr"] = world.radarr_handler
    fake_http["http://sonarr"] = world.sonarr_handler
    fake_http["http://qbit"] = world.qbit_handler(w)

    asyncio.run(run_scan())

    [row] = _rows(session)
    medias = list(session.exec(select(Media)).all())
    assert row.day == today()
    assert row.media_count == len(medias) > 0
    assert row.total_size == sum(media.total_size for media in medias)


def test_a_partial_scan_records_nothing(fake_http, settings, tmp_path):
    """Une analyse par service ne couvre qu'une partie des médias."""
    seed_library(tmp_path)
    with Session(engine) as db:
        _full_scan_done(db)
    fake_http["http://qbit"] = qbit_handler([])

    asyncio.run(run_service_scan("torrents"))

    with Session(engine) as db:
        assert _rows(db) == []


def test_ten_thousand_media_are_summed_quickly(session, settings):
    _full_scan_done(session)
    session.add_all(
        Media(media_type=MediaType.movie if i % 3 else MediaType.series, title=f"Média {i}", total_size=i)
        for i in range(10_000)
    )
    session.commit()

    started = time.monotonic()
    take_snapshot(session, settings, DAY)
    elapsed = time.monotonic() - started

    [row] = _rows(session)
    assert row.media_count == 10_000
    assert row.total_size == sum(range(10_000))
    assert row.series_count == len(range(0, 10_000, 3))
    assert elapsed < 1.0, f"{elapsed:.2f} s"


# --- Routes ---------------------------------------------------------------------


def test_the_route_returns_the_history_with_disk_numbers(admin_client, session, settings):
    _full_scan_done(session)
    _media(session, MediaType.movie, 100)
    take_snapshot(session, settings)

    response = admin_client.get("/api/library/history")

    assert response.status_code == 200
    [snapshot] = response.json()
    assert snapshot["day"] == today().isoformat()
    assert (snapshot["media_count"], snapshot["movie_size"]) == (1, 100)
    library, downloads = snapshot["disks"]
    # Les deux racines de test vivent sur le même disque : même numéro.
    assert library["disk"] == downloads["disk"] == 0
    assert "device" not in library


def test_disks_on_distinct_filesystems_get_distinct_numbers(admin_client, session):
    disks = [
        DiskUsage(role="library", path="/media", available=True, total=10, free=5, device=2**63 + 7),
        DiskUsage(role="downloads", path="/torrents", available=True, total=20, free=1, device=42),
        DiskUsage(role="downloads", path="/absent", available=False),
    ]
    session.add(LibrarySnapshot(day=today(), disks=TypeAdapter(list[DiskUsage]).dump_json(disks).decode()))
    session.commit()

    [snapshot] = admin_client.get("/api/library/history?days=1").json()

    assert [disk["disk"] for disk in snapshot["disks"]] == [0, 1, None]


@pytest.mark.parametrize("days", [0, -1, 3651, "abc"])
def test_the_range_is_bounded(admin_client, days):
    assert admin_client.get(f"/api/library/history?days={days}").status_code == 422


def test_retention_settings_are_read_and_validated(admin_client, session, settings):
    assert admin_client.get("/api/library/history/settings").json() == {
        "retention_days": 730,
        "min_days": 30,
        "max_days": 3650,
    }
    for invalid in (29, 3651, "trente"):
        assert admin_client.put("/api/library/history/settings", json={"retention_days": invalid}).status_code == 422

    response = admin_client.put("/api/library/history/settings", json={"retention_days": 60})

    assert response.status_code == 200 and response.json()["retention_days"] == 60


def test_shortening_retention_deletes_nothing_until_the_next_measure(admin_client, session, settings):
    """Le temps de revenir sur une erreur de saisie."""
    session.add(LibrarySnapshot(day=today() - timedelta(days=100)))
    session.commit()

    admin_client.put("/api/library/history/settings", json={"retention_days": 30})

    assert len(_rows(session)) == 1


def test_the_routes_require_a_session(client):
    assert client.get("/api/library/history").status_code == 401
    assert client.get("/api/library/history/settings").status_code == 401
    assert client.put("/api/library/history/settings", json={"retention_days": 60}).status_code == 401
