"""Prévisions d'espace disque et mode objectif de l'assistant (roadmap
Phase 4) : régression et fourchette, « plein dans X à Y semaines », message
honnête sous 14 jours, sélection gloutonne qui respecte les protections,
« qui perd quoi », routes et performance sur 10 000 médias."""

import json
import math
import time
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel import Session

from app.models.ignore import IgnoreRule
from app.models.library_snapshot import LibrarySnapshot
from app.models.media import EmbyUser, Media, MediaType, MediaWatch
from app.schemas.cleanup import CleanupPlanRequest
from app.services.cleanup_plan import MAX_PLAN_ITEMS, PlanError, build_plan, select_for_goal
from app.services.cleanup_score import PRESETS, evaluate
from app.services.forecast import (
    HORIZON_DAYS,
    MAX_FILL_DAYS,
    MIN_HISTORY_DAYS,
    Snapshot,
    Trend,
    bytes_to_free,
    fill_estimate,
    fit_trend,
    forecast,
    t_value,
)
from app.services.ignores import media_key_of
from app.services.library_history import DiskUsage, today
from tests.test_cleanup import facts

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


def test_bytes_to_free_uses_the_prudent_slope_and_the_margin():
    trend = Trend(per_day=10, low=5, high=20, points=20)
    # 30 jours à 20 o/j = 600, 100 libres : 500 manquent, + 5 %.
    assert bytes_to_free(100, trend, 30, 0.05) == 525
    assert bytes_to_free(1000, trend, 30, 0.05) == 0


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


# --- Mode objectif : sélection pure ----------------------------------------------


def _ranked(*sizes: int):
    return [
        (evaluate(facts(media_id=i, title=f"M{i}", reclaimable_bytes=size), PRESETS["balanced"], datetime.now(UTC)), 0)
        for i, size in enumerate(sizes, 1)
    ]


def test_the_greedy_selection_follows_the_ranking():
    selection = select_for_goal(_ranked(4, 5, 6, 7), 9, 10)
    assert [e.facts.media_id for e, _ in selection.chosen] == [1, 2]
    assert selection.freed == 9 and not selection.limited


def test_media_made_useless_by_a_bigger_one_are_dropped():
    selection = select_for_goal(_ranked(3, 3, 50), 10, 10)
    assert [e.facts.media_id for e, _ in selection.chosen] == [3]
    assert selection.freed == 50


def test_a_media_that_frees_nothing_is_never_chosen():
    selection = select_for_goal(_ranked(0, 5), 1, 10)
    assert [e.facts.media_id for e, _ in selection.chosen] == [2]


def test_the_selection_is_capped():
    selection = select_for_goal(_ranked(*([1] * 5)), 100, 3)
    assert len(selection.chosen) == 3 and selection.limited and selection.freed == 3


# --- Mode objectif : base et route -----------------------------------------------


def _media(session: Session, title: str, size: int, **fields) -> Media:
    media = Media(
        media_type=fields.pop("media_type", MediaType.movie),
        title=title,
        total_size=size,
        full_reclaimable_bytes=size,
        emby_date_added=datetime.now(UTC) - timedelta(days=fields.pop("added_days_ago", 400)),
        **fields,
    )
    session.add(media)
    session.commit()
    return media


def _user(session: Session, user_id: str, *, active_days_ago: int | None = 1, disabled: bool = False) -> None:
    last = None if active_days_ago is None else datetime.now(UTC) - timedelta(days=active_days_ago)
    session.add(EmbyUser(id=user_id, name=user_id.capitalize(), is_disabled=disabled, last_activity_at=last))
    session.commit()


def test_free_goal_respects_the_protections_and_says_who_loses_what(admin_client, session, settings):
    old = _media(session, "Ancien", 30 * GB)
    _media(session, "Récent", 80 * GB, added_days_ago=2)  # protégé : ajouté récemment
    excluded = _media(session, "Exclu", 90 * GB, radarr_id=9)
    key = media_key_of(MediaType.movie, 9, None, None)
    session.add(IgnoreRule(kind="cleanup", media_key=key, media_title="Exclu", media_type="movie", target=key))
    big = _media(session, "Gros", 40 * GB)
    _user(session, "lea", active_days_ago=300)  # inactive : son favori ne protège plus
    _user(session, "tom")
    _user(session, "off", disabled=True)
    session.add(MediaWatch(media_id=big.id, emby_user_id="lea", favorite=True))
    session.add(MediaWatch(media_id=old.id, emby_user_id="tom", in_progress=True, progress=40))
    session.add(MediaWatch(media_id=old.id, emby_user_id="off", in_progress=True, progress=10))
    session.commit()

    body = admin_client.post("/api/cleanup/plan", json={"goal": "free", "target_bytes": 60 * GB}).json()

    chosen = {item["title"] for item in body["items"]}
    assert chosen == {"Ancien", "Gros"}
    assert excluded.id not in {item["media_id"] for item in body["items"]}
    assert body["freed_bytes"] == 70 * GB and body["shortfall_bytes"] == 0
    losses = {loss["name"]: loss["media"] for loss in body["losses"]}
    assert set(losses) == {"Lea", "Tom"}
    assert losses["Lea"][0]["favorite"] and losses["Lea"][0]["title"] == "Gros"
    assert losses["Tom"][0]["in_progress"] and losses["Tom"][0]["progress"] == 40


def test_free_goal_reports_what_is_missing(admin_client, session, settings):
    _media(session, "Seul", 10 * GB)
    body = admin_client.post("/api/cleanup/plan", json={"goal": "free", "target_bytes": 50 * GB}).json()
    assert body["freed_bytes"] == 10 * GB and body["shortfall_bytes"] == 40 * GB
    assert not body["limited"] and body["max_items"] == MAX_PLAN_ITEMS


def test_until_goal_frees_the_prudent_growth(admin_client, session, settings):
    # 405 Go libres, 5 Go/j sans incertitude : 30 jours = 150 Go, rien à libérer.
    _store_history(session, 20)
    _media(session, "Film", 100 * GB)
    until = (today() + timedelta(days=30)).isoformat()
    body = admin_client.post("/api/cleanup/plan", json={"goal": "until", "until": until, "disk": "library+downloads"})
    assert body.status_code == 200
    assert body.json()["target_bytes"] == 0 and body.json()["items"] == []

    # 100 jours = 500 Go : 95 Go manquent, + 5 %.
    until = (today() + timedelta(days=100)).isoformat()
    plan = admin_client.post(
        "/api/cleanup/plan", json={"goal": "until", "until": until, "disk": "library+downloads"}
    ).json()
    assert plan["target_bytes"] == math.ceil(95 * GB * 1.05)
    assert plan["days"] == 100 and plan["free_bytes"] == 405 * GB
    assert [item["title"] for item in plan["items"]] == ["Film"]


def test_until_goal_needs_enough_history(admin_client, session, settings):
    _store_history(session, 5)
    until = (today() + timedelta(days=30)).isoformat()
    payload = {"goal": "until", "until": until, "disk": "library+downloads"}
    response = admin_client.post("/api/cleanup/plan", json=payload)
    assert response.status_code == 422
    assert "5 jours mesurés sur 14" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"goal": "free"},
        {"goal": "free", "target_bytes": 0},
        {"goal": "free", "target_bytes": 10**16},
        {"goal": "until", "disk": "library"},
        {"goal": "until", "until": "2030-01-01", "disk": "/etc/passwd"},
        {"goal": "everything", "target_bytes": GB},
    ],
)
def test_the_plan_request_is_validated(admin_client, payload):
    assert admin_client.post("/api/cleanup/plan", json=payload).status_code == 422


def test_until_goal_refuses_a_past_or_far_date(session, settings):
    _store_history(session, 20)
    for until in (today(), today() + timedelta(days=400)):
        with pytest.raises(PlanError):
            build_plan(session, CleanupPlanRequest(goal="until", until=until, disk="library+downloads"))


def test_an_unknown_disk_is_refused(session, settings):
    _store_history(session, 20)
    with pytest.raises(PlanError):
        build_plan(session, CleanupPlanRequest(goal="until", until=today() + timedelta(days=9), disk="downloads"))


def test_the_plan_writes_nothing(admin_client, session, settings):
    media = _media(session, "Film", 10 * GB)
    admin_client.post("/api/cleanup/plan", json={"goal": "free", "target_bytes": GB})
    session.expire_all()
    assert session.get(Media, media.id) is not None


def test_the_routes_require_a_session(client):
    assert client.get("/api/library/forecast").status_code == 401
    assert client.post("/api/cleanup/plan", json={"goal": "free", "target_bytes": GB}).status_code == 401


# --- Performance ------------------------------------------------------------------


def test_a_plan_over_ten_thousand_media_stays_fast(session, settings):
    now = datetime.now(UTC)
    for u in range(8):
        session.add(EmbyUser(id=f"u{u}", name=f"User {u}", last_activity_at=now - timedelta(days=u * 30)))
    medias = [
        Media(
            media_type=MediaType.series if i % 3 == 0 else MediaType.movie,
            title=f"Média {i}",
            total_size=GB * (i % 50 + 1),
            full_reclaimable_bytes=GB * (i % 50 + 1),
            emby_date_added=now - timedelta(days=60 + i % 900),
            last_played_at=now - timedelta(days=i % 400) if i % 4 else None,
        )
        for i in range(10_000)
    ]
    session.add_all(medias)
    session.commit()
    session.add_all(
        MediaWatch(media_id=m.id, emby_user_id=f"u{u}", in_progress=(m.id + u) % 7 == 0, favorite=(m.id + u) % 89 == 0)
        for m in medias
        for u in range(8)
    )
    session.commit()
    request = CleanupPlanRequest(goal="free", target_bytes=2000 * GB)

    timings = []
    for _ in range(3):
        started = time.monotonic()
        plan = build_plan(session, request)
        timings.append(time.monotonic() - started)

    assert plan.freed_bytes >= 2000 * GB and plan.items
    assert min(timings) < 0.5, [f"{t:.2f}" for t in timings]
