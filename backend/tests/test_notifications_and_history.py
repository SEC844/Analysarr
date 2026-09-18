import asyncio
import json
import os

import httpx

from app.database import _migrate_legacy_notifications
from app.models.activity import ActionLog
from app.models.notification_channel import NotificationChannel
from app.models.settings import Settings
from app.schemas.media import DeleteFootprintItem, DeleteStepResult, DiskUnit, MediaDeleteFootprint
from app.services import action_log, notifications
from app.services.action_log import MediaRef, record_action
from app.services.hardlink_repair import _separate_copy_size
from app.services.media_delete import reclaimed_bytes
from app.services.notifications import (
    ChannelTarget,
    action_notification,
    build_test_notification,
    notify,
    orphan_notification,
    scan_completed_notification,
    scan_failed_notification,
    send,
)

WEBHOOK = "https://discord.com/api/webhooks/123/secret-token"
CHANNELS = "/api/notifications/channels"
MATRIX = MediaRef(id=1, title="Matrix", media_type="movie", year=1999, emby_item_id="42", poster_image_tag="t1")
STEPS = [
    DeleteStepResult(kind="torrent", label="Matrix.1999.1080p.mkv", success=True),
    DeleteStepResult(
        kind="library_file", label="/data/media/movies/Matrix (1999)/Matrix.mkv", success=False, error="Permission refusée"
    ),
]
PNG = b"\x89PNG\r\n\x1a\nposter"


def discord_target(events: tuple[str, ...] = ("delete_selection",), name: str = "Discord") -> ChannelTarget:
    return ChannelTarget(id=1, kind="discord", name=name, url=WEBHOOK, token=None, events=events)


def discord_payload(req: httpx.Request) -> dict:
    if req.headers["content-type"].startswith("application/json"):
        return json.loads(req.content)
    body = req.content.decode("latin-1")
    part = body.split('name="payload_json"', 1)[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0]
    return json.loads(part.encode("latin-1").decode("utf-8"))


def create_channel(client, **overrides):
    body = {"kind": "discord", "name": "Admin", "url": WEBHOOK, "events": ["scan_failed"]} | overrides
    return client.post(CHANNELS, json=body)


def test_channels_keep_their_secrets_write_only(admin_client, session):
    created = create_channel(admin_client)
    assert created.status_code == 201
    channel = created.json()
    assert channel["url"] is None and channel["url_set"] and not channel["token_set"]
    assert "secret-token" not in created.text and "secret-token" not in admin_client.get(CHANNELS).text

    renamed = admin_client.put(
        f"{CHANNELS}/{channel['id']}",
        json={"kind": "discord", "name": "Scans", "url": "", "events": ["scan_completed", "scan_failed"]},
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "Scans"
    assert renamed.json()["events"] == ["scan_completed", "scan_failed"]
    session.expire_all()
    assert session.get(NotificationChannel, channel["id"]).url == WEBHOOK  # URL conservée

    assert admin_client.delete(f"{CHANNELS}/{channel['id']}").status_code == 204
    assert admin_client.get(CHANNELS).json() == []


def test_invalid_channels_are_rejected(admin_client):
    assert create_channel(admin_client, url="https://evil.example/api/webhooks/1/x").status_code == 400
    assert create_channel(admin_client, kind="ntfy", url="ftp://ntfy.sh/topic").status_code == 400
    assert create_channel(admin_client, kind="gotify", url="http://gotify").status_code == 400
    assert create_channel(admin_client, events=["tout"]).status_code == 400
    assert create_channel(admin_client, name="   ").status_code == 400

    channel = create_channel(admin_client).json()
    kind_change = admin_client.put(
        f"{CHANNELS}/{channel['id']}", json={"kind": "ntfy", "name": "Admin", "url": "https://ntfy.sh/topic", "events": []}
    )
    assert kind_change.status_code == 400
    assert admin_client.get(CHANNELS).json()[0]["kind"] == "discord"


def test_each_channel_only_receives_the_events_it_subscribed_to(fake_http):
    discord, ntfy = [], []
    fake_http["https://discord.com"] = lambda req: discord.append(req) or httpx.Response(204)
    fake_http["https://ntfy.sh"] = lambda req: ntfy.append(req) or httpx.Response(200)
    targets = [
        discord_target(events=("scan_completed",), name="Scans"),
        ChannelTarget(id=2, kind="ntfy", name="Actions", url="https://ntfy.sh/topic", token=None, events=("delete_selection",)),
    ]

    async def run():
        summary = scan_completed_notification(
            "fr", media=1, duplicates=0, orphans=0, reclaimable_bytes=0, matched=0, torrents=0, duration_seconds=1
        )
        notify(targets, "scan_completed", summary)
        deletion = action_notification("fr", "delete_selection", MATRIX, success=1, failures=0, freed_bytes=None, steps=STEPS[:1])
        notify(targets, "delete_selection", deletion)
        notify(targets, "hardlink_repair", deletion)  # aucun canal abonné
        await asyncio.gather(*notifications._pending)

    asyncio.run(run())

    assert [discord_payload(req)["embeds"][0]["title"] for req in discord] == ["Scan terminé"]
    assert len(ntfy) == 1 and ntfy[0].url.params["title"] == "Suppression effectuée"


def test_action_notification_carries_media_space_and_steps():
    n = action_notification("fr", "delete_selection", MATRIX, success=1, failures=1, freed_bytes=5 * 1024**3, steps=STEPS)
    assert n.title == "Suppression effectuée" and n.description == "Matrix (1999)" and n.level == "warning"
    assert ("Espace libéré", "5.0 Go") in n.fields and ("Échecs", "1") in n.fields
    assert n.details == ["✅ Matrix.1999.1080p.mkv", "❌ /data/media/movies/Matrix (1999)/Matrix.mkv — Permission refusée"]

    many = action_notification("en", "hardlink_repair", MATRIX, success=12, failures=0, freed_bytes=None, steps=[STEPS[0]] * 12)
    assert many.level == "success" and len(many.details) == 11 and many.details[-1] == "… and 2 more"
    assert all(name != "Space freed" for name, _ in many.fields)


def test_orphan_notification_lists_the_new_orphans():
    n = orphan_notification("fr", [("Matrix", 5 * 1024**3), ("Dune", 1024**3)])
    assert n.title == "Nouveaux torrents orphelins" and n.level == "warning"
    assert ("Médias concernés", "2") in n.fields and ("Espace récupérable", "6.0 Go") in n.fields
    assert n.details == ["• Matrix — 5.0 Go", "• Dune — 1.0 Go"]


def test_scan_summary_notification():
    n = scan_completed_notification(
        "fr", media=120, duplicates=3, orphans=2, reclaimable_bytes=3 * 1024**3, matched=98, torrents=100, duration_seconds=75
    )
    assert n.level == "warning"
    assert ("Espace récupérable", "3.0 Go") in n.fields
    assert ("Torrents rattachés", "98/100") in n.fields and ("Durée", "1 min 15 s") in n.fields


def test_discord_embed_attaches_the_poster(fake_http):
    received = []
    fake_http["https://discord.com"] = lambda req: received.append(req) or httpx.Response(204)
    n = action_notification("en", "cascade_delete", MATRIX, success=1, failures=0, freed_bytes=1024, steps=STEPS[:1])
    n.image = (PNG, "image/png")

    assert asyncio.run(send([discord_target()], n)) == {"Discord": None}

    [req] = received
    assert req.headers["content-type"].startswith("multipart/form-data") and PNG in req.content
    embed = discord_payload(req)["embeds"][0]
    assert embed["title"] == "Cleanup completed" and embed["thumbnail"] == {"url": "attachment://poster.png"}
    assert {"name": "Space freed", "value": "1.0 KB", "inline": True} in embed["fields"]


def test_ntfy_attaches_the_poster_and_falls_back_without_attachment_support(fake_http):
    calls = []

    def ntfy(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(400 if req.method == "PUT" else 200)

    fake_http["https://ntfy.sh"] = ntfy
    target = ChannelTarget(id=1, kind="ntfy", name="ntfy", url="https://ntfy.sh/topic", token="ntfy-secret", events=())
    n = action_notification("en", "cascade_delete", MATRIX, success=1, failures=0, freed_bytes=1024, steps=STEPS[:1])
    n.image = (PNG, "image/png")

    assert asyncio.run(send([target], n)) == {"ntfy": None}

    put, post = calls
    assert put.content == PNG and put.url.params["filename"] == "poster.png"
    assert "Matrix (1999)" in put.url.params["message"] and put.headers["Authorization"] == "Bearer ntfy-secret"
    assert post.method == "POST" and post.url.params["title"] == "Cleanup completed"
    assert "Space freed: 1.0 KB" in post.content.decode()


def test_gotify_receives_markdown(fake_http):
    received = []
    fake_http["http://gotify"] = lambda req: received.append(req) or httpx.Response(200)
    target = ChannelTarget(id=1, kind="gotify", name="Gotify", url="http://gotify/", token="gotify-secret", events=())
    n = action_notification("fr", "delete_selection", MATRIX, success=1, failures=1, freed_bytes=None, steps=STEPS)

    asyncio.run(send([target], n))

    [req] = received
    body = json.loads(req.content)
    assert req.url.path == "/message" and req.headers["X-Gotify-Key"] == "gotify-secret"
    assert body["priority"] == 7 and body["extras"]["client::display"]["contentType"] == "text/markdown"
    assert "**Échecs** : 1" in body["message"] and "- ❌ " in body["message"]


def test_send_errors_never_expose_urls_or_tokens(fake_http):
    fake_http["http://gotify"] = lambda req: httpx.Response(401)
    targets = [
        ChannelTarget(id=1, kind="ntfy", name="ntfy", url="https://unreachable/secret-topic", token=None, events=()),
        ChannelTarget(id=2, kind="gotify", name="Gotify", url="http://gotify", token="gotify-secret", events=()),
    ]

    results = asyncio.run(send(targets, build_test_notification("fr")))

    assert results == {"ntfy": "ConnectError", "Gotify": "HTTP 401"}


def test_notify_attaches_the_cached_poster(fake_http, monkeypatch):
    sent = []
    fake_http["https://discord.com"] = lambda req: sent.append(req) or httpx.Response(204)
    monkeypatch.setattr(
        notifications, "read_cached_poster", lambda item_id, tag: (PNG, "image/png") if (item_id, tag) == ("42", "t1") else None
    )

    async def run():
        deletion = action_notification("en", "delete_selection", MATRIX, success=1, failures=0, freed_bytes=None, steps=STEPS[:1])
        notify([discord_target()], "delete_selection", deletion, poster=MATRIX)
        notify([discord_target()], "scan_failed", scan_failed_notification("en", "boom"))  # canal non abonné
        await asyncio.gather(*notifications._pending)

    asyncio.run(run())

    assert len(sent) == 1 and PNG in sent[0].content


def test_channel_test_endpoint_uses_the_saved_channel(admin_client, fake_http):
    fake_http["https://discord.com"] = lambda req: httpx.Response(204)
    channel = create_channel(admin_client).json()

    assert admin_client.post(f"{CHANNELS}/{channel['id']}/test").json() == {"ok": True, "error": None}
    assert admin_client.post(f"{CHANNELS}/999/test").status_code == 404


def test_legacy_notification_settings_become_channels(session):
    row = Settings(
        id=1,
        notify_discord_webhook=WEBHOOK,
        notify_gotify_url="http://gotify",
        notify_gotify_token="gotify-secret",
        notify_on_scan=True,
        notify_on_actions=True,
    )
    session.add(row)
    session.commit()

    _migrate_legacy_notifications()
    session.expire_all()

    channels = {c.kind: c for c in session.exec(NotificationChannel.__table__.select()).all()}
    assert set(channels) == {"discord", "gotify"}
    assert channels["discord"].url == WEBHOOK and channels["gotify"].token == "gotify-secret"
    assert "scan_completed" in json.loads(channels["discord"].events)
    assert "cascade_delete" in json.loads(channels["discord"].events)
    # Les anciens champs sont vidés : un secret ne vit qu'à un seul endroit.
    migrated = session.get(Settings, 1)
    assert migrated.notify_discord_webhook is None and migrated.notify_gotify_token is None


def test_reclaimed_bytes_needs_every_link_of_a_unit():
    footprint = MediaDeleteFootprint(
        units=[DiskUnit(size=100, links=2), DiskUnit(size=50, links=1)],
        torrents=[DeleteFootprintItem(id=1, units=[0])],
        files=[DeleteFootprintItem(id=10, units=[0]), DeleteFootprintItem(id=11, units=[1])],
    )
    assert reclaimed_bytes(footprint, [1], []) == 0
    assert reclaimed_bytes(footprint, [1], [10]) == 100
    assert reclaimed_bytes(footprint, [], [10, 11]) == 50


def test_hardlink_repair_only_counts_separate_copies_as_freed(tmp_path):
    source = tmp_path / "torrent.mkv"
    source.write_bytes(b"x" * 10)
    copy = tmp_path / "library.mkv"
    copy.write_bytes(b"x" * 10)
    linked = tmp_path / "linked.mkv"
    os.link(source, linked)

    assert _separate_copy_size(str(copy), str(source)) == 10
    assert _separate_copy_size(str(linked), str(source)) == 0
    assert _separate_copy_size(str(tmp_path / "missing.mkv"), str(source)) == 0


def test_history_records_actions_and_is_bounded(admin_client, session, monkeypatch):
    monkeypatch.setattr(action_log, "MAX_ENTRIES", 3)
    steps = [
        DeleteStepResult(kind="torrent", label="Matrix.mkv", success=True),
        DeleteStepResult(kind="file", label="x", success=False, error="refusé"),
    ]
    for i in range(5):
        record_action(session, "delete_selection", MediaRef(id=999, title=f"Film {i}", media_type="movie"), steps, freed_bytes=10)

    assert len(session.exec(ActionLog.__table__.select()).all()) == 3
    entries = admin_client.get("/api/history").json()
    assert [e["media_title"] for e in entries] == ["Film 4", "Film 3", "Film 2"]
    latest = entries[0]
    assert latest["success_count"] == 1 and latest["failure_count"] == 1 and latest["freed_bytes"] == 10
    assert latest["media_id"] is None  # la fiche 999 n'existe pas
    assert latest["details"][1] == {"label": "x", "success": False, "error": "refusé"}

    assert admin_client.delete("/api/history").status_code == 204
    assert admin_client.get("/api/history").json() == []
