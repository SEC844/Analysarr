import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx

from app.models.activity import ActionLog
from app.models.automation import Automation
from app.models.media import Media, MediaType, Torrent
from app.models.notification_channel import NotificationChannel
from app.schemas.automations import AutomationConditions
from app.services import notifications
from app.services.automations import as_rule, eligible_medias, run_automations, run_rule
from app.services.notifications import channel_targets

AUTOMATIONS = "/api/automations"
WEBHOOK = "https://discord.com/api/webhooks/123/secret-token"


def qbit_handler(calls: list[httpx.Request]):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.")
        return httpx.Response(200, text="")

    return handler


def add_orphan(session, title="Matrix", *, ratio=2.0, seeded_days=30, size=1024, media_type=MediaType.movie):
    media = Media(media_type=media_type, title=title, statuses="orphelin_qbit", reclaimable_bytes=size)
    session.add(media)
    session.commit()
    session.refresh(media)
    torrent = Torrent(
        media_id=media.id,
        hash=f"hash-{media.id}",
        name=f"{title}.mkv",
        size=size,
        ratio=ratio,
        is_hardlinked=False,
        repairable=False,
        completed_on=datetime.now(timezone.utc) - timedelta(days=seeded_days),
    )
    session.add(torrent)
    session.commit()
    return media


def rule(session, **overrides) -> Automation:
    fields = {
        "name": "Nettoyage orphelins",
        "trigger": "orphan_detected",
        "action": "cleanup",
        "conditions": json.dumps({}),
        "max_actions": 5,
        "dry_run": False,
    } | overrides
    automation = Automation(**fields)
    session.add(automation)
    session.commit()
    session.refresh(automation)
    return automation


def test_conditions_filter_media_and_torrents(session):
    add_orphan(session, "Récent", seeded_days=2, ratio=0.1)
    add_orphan(session, "Ancien", seeded_days=40, ratio=3.0, size=5 * 1024**3)
    add_orphan(session, "Série", seeded_days=40, ratio=3.0, media_type=MediaType.series)

    def titles(**conditions):
        automation = Automation(
            name="r", trigger="orphan_detected", action="cleanup", conditions=json.dumps(conditions), max_actions=10
        )
        return sorted(media.title for media, _ in eligible_medias(session, as_rule(automation)))

    assert titles() == ["Ancien", "Récent", "Série"]
    assert titles(min_seed_days=30) == ["Ancien", "Série"]
    assert titles(min_ratio=1.0) == ["Ancien", "Série"]
    assert titles(media_types=["movie"], min_seed_days=30) == ["Ancien"]
    assert titles(min_reclaimable_bytes=1024**3) == ["Ancien"]


def test_conditions_never_assume_an_unknown_seed_date(session):
    media = add_orphan(session, "Sans date")
    torrent = session.exec(Torrent.__table__.select()).first()
    session.exec(Torrent.__table__.update().where(Torrent.__table__.c.id == torrent.id).values(completed_on=None, added_on=None))
    session.commit()

    automation = Automation(
        name="r", trigger="orphan_detected", action="cleanup", conditions=json.dumps({"min_seed_days": 1}), max_actions=5
    )
    assert eligible_medias(session, as_rule(automation)) == []
    assert media.title == "Sans date"


def test_cleanup_rule_deletes_orphans_and_reports(fake_http, session, settings):
    qbit_calls: list[httpx.Request] = []
    fake_http["http://qbit"] = qbit_handler(qbit_calls)
    discord: list[httpx.Request] = []
    fake_http["https://discord.com"] = lambda req: discord.append(req) or httpx.Response(204)
    session.add(
        NotificationChannel(kind="discord", name="Auto", url=WEBHOOK, events=json.dumps(["automation"]))
    )
    session.commit()
    add_orphan(session, "Matrix", size=2048)
    automation = rule(session)

    async def run():
        return await run_rule(session, settings, channel_targets(session), automation), await asyncio.gather(
            *notifications._pending
        )

    result, _ = asyncio.run(run())

    assert (result.matched, result.executed, result.freed_bytes) == (1, 1, 2048)
    assert all(step.success for step in result.steps)
    assert any(req.url.path == "/api/v2/torrents/delete" for req in qbit_calls)
    assert session.exec(Torrent.__table__.select()).all() == []
    # Tracé dans l'historique et notifié sur le canal abonné.
    history = session.exec(ActionLog.__table__.select()).all()
    assert [entry.action for entry in history] == ["automation"]
    assert len(discord) == 1
    session.expire_all()
    assert session.get(Automation, automation.id).last_run_count == 1


def test_dry_run_and_cap_never_touch_anything(fake_http, session, settings):
    qbit_calls: list[httpx.Request] = []
    fake_http["http://qbit"] = qbit_handler(qbit_calls)
    for i in range(4):
        add_orphan(session, f"Film {i}")

    dry = rule(session, name="Simulation", dry_run=True, max_actions=10)
    result = asyncio.run(run_rule(session, settings, [], dry))
    assert (result.matched, result.executed, result.dry_run) == (4, 4, True)
    assert qbit_calls == [] and len(session.exec(Torrent.__table__.select()).all()) == 4

    capped = rule(session, name="Plafonné", max_actions=2)
    result = asyncio.run(run_rule(session, settings, [], capped))
    assert (result.matched, result.executed) == (4, 2)
    assert len(session.exec(Torrent.__table__.select()).all()) == 2


def test_disabled_rules_never_run(fake_http, session, settings):
    fake_http["http://qbit"] = qbit_handler([])
    add_orphan(session)
    rule(session, enabled=False)

    assert asyncio.run(run_automations(session, settings, [])) == []
    assert len(session.exec(Torrent.__table__.select()).all()) == 1


def test_automation_api_validates_and_previews(admin_client, session, settings):
    add_orphan(session, "Matrix", size=4096)
    body = {
        "name": "Nettoyage",
        "trigger": "orphan_detected",
        "action": "cleanup",
        "conditions": {"min_seed_days": 7, "media_types": ["movie"]},
        "max_actions": 3,
        "dry_run": True,
    }
    created = admin_client.post(AUTOMATIONS, json=body)
    assert created.status_code == 201
    automation = created.json()
    assert automation["conditions"]["min_seed_days"] == 7 and automation["max_actions"] == 3

    assert admin_client.post(AUTOMATIONS, json={**body, "name": "  "}).status_code == 400
    assert admin_client.post(AUTOMATIONS, json={**body, "action": "repair_hardlinks"}).status_code == 400
    assert admin_client.post(AUTOMATIONS, json={**body, "max_actions": 999}).status_code == 422

    preview = admin_client.get(f"{AUTOMATIONS}/{automation['id']}/preview").json()
    assert preview["matched"] == 1 and preview["executed"] == 0 and preview["freed_bytes"] == 4096

    assert admin_client.put(f"{AUTOMATIONS}/{automation['id']}", json={**body, "enabled": False}).json()["enabled"] is False
    assert admin_client.delete(f"{AUTOMATIONS}/{automation['id']}").status_code == 204
    assert admin_client.get(AUTOMATIONS).json() == []


def test_conditions_out_of_scope_are_dropped_on_save(admin_client):
    """Un import bloqué n'a ni seed ni ratio : ces conditions ne doivent pas
    être enregistrées, sinon elles filtreraient sans que rien ne l'explique."""
    body = {
        "name": "Imports",
        "trigger": "import_failed_detected",
        "action": "retry_import",
        "conditions": {"media_types": ["series"], "min_seed_days": 30, "min_ratio": 1.5, "min_reclaimable_bytes": 10},
        "max_actions": 5,
        "dry_run": False,
    }
    created = admin_client.post("/api/automations", json=body).json()
    assert created["conditions"]["media_types"] == ["series"]
    assert created["conditions"]["min_seed_days"] is None
    assert created["conditions"]["min_ratio"] is None
    assert created["conditions"]["min_reclaimable_bytes"] is None

    kept = admin_client.post("/api/automations", json=body | {"trigger": "orphan_detected", "action": "cleanup"}).json()
    assert kept["conditions"]["min_seed_days"] == 30 and kept["conditions"]["min_ratio"] == 1.5
