"""Prévisions d'espace disque (roadmap Phase 4) : régression et fourchette,
« plein dans X à Y semaines », message honnête sous 14 jours, route."""

import json
import math
from datetime import date, timedelta

import pytest
from sqlmodel import Session

from app.models.library_snapshot import LibrarySnapshot
from app.services.forecast import (
    HORIZON_DAYS,
    MAX_FILL_DAYS,
    MIN_HISTORY_DAYS,
    Snapshot,
    Trend,
    fill_estimate,
    fit_trend,
    forecast,
    t_value,
)
from app.services.library_history import DiskUsage, today

GB = 1024**3
DAY = date(2026, 9, 29)


# --- Régression -----------------------------------------------------------------


def test_a_perfectly_regular_growth_has_no_uncertainty():
    points = [(DAY + timedelta(days=i), 1000 + 50 * i) for i in range(20)]
    trend = fit_trend(points)
    assert trend is not None
    assert math.isclose(trend.per_day, 50)
    assert math.isclose(trend.low, 50) and math.isclose(trend.high, 50)
    assert trend.points == 20


def test_an_irregular_growth_widens_the_range():
    # Gros téléchargements ponctuels : même pente moyenne, fourchette large.
    steady = [(DAY + timedelta(days=i), 100 * i) for i in range(20)]
    bursts = [(DAY + timedelta(days=i), 100 * i + (600 if i % 5 == 0 else -150)) for i in range(20)]
    calm, jumpy = fit_trend(steady), fit_trend(bursts)
    assert calm is not None and jumpy is not None
    assert jumpy.low < jumpy.per_day < jumpy.high
    assert jumpy.high - jumpy.low > calm.high - calm.low


def test_the_slope_needs_three_distinct_days():
    assert fit_trend([(DAY, 1), (DAY + timedelta(days=1), 2)]) is None
    assert fit_trend([(DAY, 1), (DAY, 2), (DAY, 3)]) is None


def test_student_quantiles():
    assert t_value(1) == 6.314
    assert t_value(12) == 1.782
    assert t_value(200) == 1.645
    with pytest.raises(ValueError):
        t_value(0)


# --- Remplissage ----------------------------------------------------------------


def test_full_between_the_high_and_the_low_slope():
    fill = fill_estimate(700, Trend(per_day=10, low=7, high=14, points=20))
    assert fill is not None
    assert fill.earliest_days == 50
    assert fill.latest_days == 100


def test_no_latest_date_when_the_low_slope_does_not_grow():
    fill = fill_estimate(700, Trend(per_day=2, low=-1, high=7, points=20))
    assert fill is not None
    assert (fill.earliest_days, fill.latest_days) == (100, None)


def test_a_disk_that_does_not_grow_is_never_full():
    assert fill_estimate(700, Trend(per_day=-3, low=-5, high=0, points=20)) is None
    # Plein dans plus de dix ans : pas une date utile.
    assert fill_estimate(int(MAX_FILL_DAYS * 10 + 10), Trend(per_day=10, low=9, high=10, points=20)) is None


# --- Prévision complète -----------------------------------------------------------


def _usage(role: str, used: int, total: int = 1000 * GB, device: int | None = 7, available: bool = True):
    if not available:
        return DiskUsage(role=role, path=f"/data/{role}", available=False)
    return DiskUsage(role=role, path=f"/data/{role}", available=True, total=total, free=total - used, device=device)


def _snapshots(days: int, used_per_day: int = 5 * GB, *, start_used: int = 500 * GB, split: bool = False):
    snapshots = []
    for i in range(days):
        used = start_used + used_per_day * i
        disks = [_usage("library", used), _usage("downloads", used // 2 if split else used, device=8 if split else 7)]
        snapshots.append(Snapshot(DAY - timedelta(days=days - 1 - i), 100 * GB + GB * i, disks))
    return snapshots


def test_under_fourteen_days_there_is_no_trend_but_the_history_is_shown():
    result = forecast(_snapshots(MIN_HISTORY_DAYS - 1))
    assert not result.enough_history
    assert result.history_days == MIN_HISTORY_DAYS - 1
    assert result.library_trend is None
    [disk] = result.disks
    assert disk.trend is None and disk.fill is None and disk.projection == []
    assert len(disk.history) == MIN_HISTORY_DAYS - 1


def test_a_disk_shared_by_the_library_and_the_downloads_is_forecast_once():
    result = forecast(_snapshots(20))
    assert result.enough_history
    [disk] = result.disks
    assert disk.key == "library+downloads"
    assert disk.paths == ["/data/library", "/data/downloads"]
    assert disk.trend is not None and math.isclose(disk.trend.per_day, 5 * GB)
    # 1000 - (500 + 19 × 5) = 405 Go libres, 5 Go/j : 81 jours.
    assert disk.fill is not None and math.isclose(disk.fill.earliest_days, 81)
    assert result.library_trend is not None and math.isclose(result.library_trend.per_day, GB)


def test_two_disks_get_two_forecasts():
    result = forecast(_snapshots(20, split=True))
    assert [d.key for d in result.disks] == ["library", "downloads"]


def test_the_projection_starts_from_today_and_stops_at_the_capacity():
    [disk] = forecast(_snapshots(20)).disks
    first, last = disk.projection[0], disk.projection[-1]
    assert first.day == DAY and first.used == disk.used
    assert last.day == DAY + timedelta(days=HORIZON_DAYS - HORIZON_DAYS % 7)
    # Plein au bout de 81 jours : la projection ne dépasse jamais le disque.
    assert last.used == last.high == disk.total


def test_a_root_missing_today_has_no_forecast():
    snapshots = _snapshots(20)
    snapshots[-1] = Snapshot(DAY, 0, [_usage("library", 0, available=False), _usage("downloads", 0, available=False)])
    result = forecast(snapshots)
    assert {d.available for d in result.disks} == {False}
    assert all(d.trend is None for d in result.disks)


def test_no_snapshot_no_forecast():
    result = forecast([])
    assert (result.latest_day, result.disks, result.enough_history) == (None, [], False)


# --- Route de prévision ----------------------------------------------------------


def _store_history(session: Session, days: int, used_per_day: int = 5 * GB, total: int = 1000 * GB) -> None:
    last = today()
    for i in range(days):
        used = 500 * GB + used_per_day * i
        disks = [
            {"role": "library", "path": "/media", "available": True, "total": total, "free": total - used, "device": 3},
            {"role": "downloads", "path": "/downloads", "available": True, "total": total, "free": total - used,
             "device": 3},
        ]  # fmt: skip
        session.add(
            LibrarySnapshot(day=last - timedelta(days=days - 1 - i), total_size=GB * i, disks=json.dumps(disks))
        )
    session.commit()


def test_the_forecast_route(admin_client, session):
    _store_history(session, 20)
    body = admin_client.get("/api/library/forecast").json()
    assert body["enough_history"] and body["min_history_days"] == MIN_HISTORY_DAYS
    [disk] = body["disks"]
    assert disk["key"] == "library+downloads"
    assert disk["fill"]["earliest_date"] == (today() + timedelta(days=81)).isoformat()
    assert disk["used"] == 595 * GB
    assert len(disk["history"]) == 20 and disk["projection"]


def test_the_forecast_route_is_honest_without_history(admin_client, session):
    _store_history(session, 5)
    body = admin_client.get("/api/library/forecast").json()
    assert not body["enough_history"]
    assert body["history_days"] == 5
    assert body["disks"][0]["trend"] is None


def test_the_route_requires_a_session(client):
    assert client.get("/api/library/forecast").status_code == 401
