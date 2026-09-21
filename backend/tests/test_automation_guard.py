"""Garde-fou des automatisations : un scan qui fait basculer une part anormale
de la bibliothèque suspend les règles jusqu'à une reprise manuelle."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models.activity import ActionLog
from app.models.media import Media, MediaType, ScanRun, ScanStatus
from app.models.settings import Settings
from app.services.automation_guard import detect_mass_change, is_paused, pause_automations


def add_run(session, *, media=100, duplicates=0, orphans=0, non_hardlink=0, scope="full", minutes_ago=10):
    finished = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    run = ScanRun(
        status=ScanStatus.completed,
        scope=scope,
        started_at=finished,
        finished_at=finished,
        media_count=media,
        duplicate_count=duplicates,
        orphan_count=orphans,
        non_hardlink_count=non_hardlink,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_a_massive_flip_is_detected(session, settings):
    add_run(session, orphans=3)
    latest = add_run(session, orphans=45, minutes_ago=0)

    change = detect_mass_change(session, latest, settings)
    assert change is not None
    assert (change.status, change.previous, change.current, change.percent) == ("orphelin_qbit", 3, 45, 42)


def test_a_massive_drop_never_pauses_anything(session, settings):
    """Le montage revient, les orphelins disparaissent : rien à suspendre, les
    règles n'ont plus de cible."""
    add_run(session, orphans=60)
    latest = add_run(session, orphans=1, minutes_ago=0)

    assert detect_mass_change(session, latest, settings) is None


def test_ordinary_variations_never_pause_anything(session, settings):
    add_run(session, orphans=3, duplicates=10, non_hardlink=4)
    latest = add_run(session, orphans=8, duplicates=14, non_hardlink=2, minutes_ago=0)

    assert detect_mass_change(session, latest, settings) is None


def test_a_user_action_explains_the_change(session, settings):
    previous = add_run(session, orphans=2)
    session.add(
        ActionLog(
            action="cascade_delete",
            media_title="Titre",
            created_at=previous.finished_at + timedelta(minutes=1),
            success_count=1,
        )
    )
    session.commit()
    latest = add_run(session, orphans=60, minutes_ago=0)

    assert detect_mass_change(session, latest, settings) is None


def test_a_service_scan_is_never_compared_with_a_full_scan(session, settings):
    add_run(session, media=100, orphans=2)
    partial = add_run(session, media=4, orphans=4, scope="radarr", minutes_ago=1)
    latest = add_run(session, media=100, orphans=3, minutes_ago=0)

    # La référence reste le dernier scan COMPLET : l'analyse par service, qui
    # ne couvre que quelques médias, ne doit pas servir de point de comparaison.
    assert detect_mass_change(session, latest, settings) is None
    assert partial.scope == "radarr"


def test_the_threshold_is_configurable_with_a_floor(session, settings):
    add_run(session, orphans=0)
    latest = add_run(session, orphans=10, minutes_ago=0)  # 10 % de la bibliothèque

    assert detect_mass_change(session, latest, settings) is None
    settings.automation_guard_percent = 8
    assert detect_mass_change(session, latest, settings) is not None
    settings.automation_guard_percent = 1  # sous le plancher : ramené à 5 %
    assert detect_mass_change(session, latest, settings).percent == 10


def test_paused_automations_stop_running_and_resume_on_demand(admin_client, session, settings):
    add_run(session, orphans=1)
    latest = add_run(session, orphans=50, minutes_ago=0)
    change = detect_mass_change(session, latest, settings)
    pause_automations(session, settings, change)

    guard = admin_client.get("/api/automations/guard").json()
    assert guard["paused"] and guard["status"] == "orphelin_qbit" and guard["current"] == 50

    resumed = admin_client.post("/api/automations/guard/resume").json()
    assert not resumed["paused"]
    session.expire_all()
    assert not is_paused(session.get(Settings, 1))


def test_the_scan_pauses_the_automations_and_notifies(session, settings, monkeypatch):
    """Le scan complet met en pause avant d'exécuter la moindre règle."""
    from app.services import scan

    add_run(session, orphans=1)
    latest = add_run(session, orphans=50, minutes_ago=0)
    ran = []
    monkeypatch.setattr(
        "app.services.automations.run_automations",
        lambda *args, **kwargs: ran.append(True),
    )
    sent = []
    monkeypatch.setattr(scan, "notify", lambda channels, event, notification, *a, **k: sent.append(event))

    asyncio.run(scan._run_automations([], latest.id))

    assert ran == []  # aucune règle exécutée
    assert sent == ["automations_paused"]
    session.expire_all()
    assert is_paused(session.get(Settings, 1))


def test_a_guarded_percentage_is_stored_within_bounds(admin_client, settings, session):
    assert admin_client.put("/api/automations/guard", json={"percent": 35}).json()["percent"] == 35
    assert admin_client.put("/api/automations/guard", json={"percent": 2}).status_code == 422
    assert admin_client.put("/api/automations/guard", json={"percent": 150}).status_code == 422
    session.expire_all()
    assert session.get(Settings, 1).automation_guard_percent == 35


def test_media_rows_are_untouched_by_the_guard(session, settings):
    session.add(Media(media_type=MediaType.movie, title="Titre", statuses="orphelin_qbit"))
    session.commit()
    add_run(session, orphans=1)
    latest = add_run(session, orphans=50, minutes_ago=0)
    pause_automations(session, settings, detect_mass_change(session, latest, settings))

    assert session.exec(select(Media)).first().statuses == "orphelin_qbit"
