import asyncio
import json
import os

import httpx

from app.models.activity import ActionLog
from app.models.settings import Settings
from app.schemas.media import DeleteFootprintItem, DeleteStepResult, DiskUnit, MediaDeleteFootprint
from app.services import action_log, notifications
from app.services.action_log import MediaRef, record_action
from app.services.hardlink_repair import _separate_copy_size
from app.services.media_delete import reclaimed_bytes
from app.services.notifications import (
    Targets,
    action_notification,
    build_test_notification,
    notify,
    scan_completed_notification,
    scan_failed_notification,
    send,
)

WEBHOOK = "https://discord.com/api/webhooks/123/secret-token"
MATRIX = MediaRef(id=1, title="Matrix", media_type="movie", year=1999, emby_item_id="42", poster_image_tag="t1")
STEPS = [
    DeleteStepResult(kind="torrent", label="Matrix.1999.1080p.mkv", success=True),
    DeleteStepResult(
        kind="library_file", label="/data/media/movies/Matrix (1999)/Matrix.mkv", success=False, error="Permission refusée"
    ),
]
PNG = b"\x89PNG\r\n\x1a\nposter"


def discord_payload(req: httpx.Request) -> dict:
    if req.headers["content-type"].startswith("application/json"):
        return json.loads(req.content)
    body = req.content.decode("latin-1")
    part = body.split('name="payload_json"', 1)[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0]
    return json.loads(part.encode("latin-1").decode("utf-8"))


def test_notification_secrets_are_write_only_and_kept_when_empty(admin_client, settings):
    body = {"notify_discord_webhook": WEBHOOK, "notify_gotify_url": "http://gotify", "notify_gotify_token": "gotify-secret"}
    res = admin_client.put("/api/settings", json=body)
    assert res.status_code == 200
    saved = res.json()["notifications"]
    assert saved["discord_set"] and not saved["ntfy_set"] and saved["gotify_url"] == "http://gotify"
    assert "secret-token" not in res.text and "gotify-secret" not in res.text

    kept = admin_client.put("/api/settings", json={"notify_gotify_url": "http://gotify"}).json()["notifications"]
    assert kept["discord_set"] and kept["gotify_token_set"]

    cleared = admin_client.put("/api/settings", json={"notify_clear": ["discord"]}).json()["notifications"]
    assert not cleared["discord_set"] and cleared["gotify_token_set"]


def test_only_official_discord_webhooks_and_http_urls_are_accepted(admin_client, settings):
    for body in (
        {"notify_discord_webhook": "https://evil.example/api/webhooks/1/x"},
        {"notify_ntfy_url": "file:///etc/passwd"},
        {"notify_gotify_url": "gopher://gotify"},
    ):
        assert admin_client.put("/api/settings", json=body).status_code == 400


def test_action_notification_carries_media_space_and_steps():
    n = action_notification("fr", "delete_selection", MATRIX, success=1, failures=1, freed_bytes=5 * 1024**3, steps=STEPS)
    assert n.title == "Suppression effectuée" and n.description == "Matrix (1999)" and n.level == "warning"
    assert ("Espace libéré", "5.0 Go") in n.fields and ("Échecs", "1") in n.fields
    assert n.details == ["✅ Matrix.1999.1080p.mkv", "❌ /data/media/movies/Matrix (1999)/Matrix.mkv — Permission refusée"]

    many = action_notification("en", "hardlink_repair", MATRIX, success=12, failures=0, freed_bytes=None, steps=[STEPS[0]] * 12)
    assert many.level == "success" and len(many.details) == 11 and many.details[-1] == "… and 2 more"
    assert all(name != "Space freed" for name, _ in many.fields)


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

    assert asyncio.run(send(Targets(WEBHOOK, None, None, None, None), n)) == {"discord": None}

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
    n = action_notification("en", "cascade_delete", MATRIX, success=1, failures=0, freed_bytes=1024, steps=STEPS[:1])
    n.image = (PNG, "image/png")

    assert asyncio.run(send(Targets(None, "https://ntfy.sh/topic", "ntfy-secret", None, None), n)) == {"ntfy": None}

    put, post = calls
    assert put.content == PNG and put.url.params["filename"] == "poster.png"
    assert "Matrix (1999)" in put.url.params["message"] and put.headers["Authorization"] == "Bearer ntfy-secret"
    assert post.method == "POST" and post.url.params["title"] == "Cleanup completed"
    assert "Space freed: 1.0 KB" in post.content.decode()


def test_gotify_receives_markdown(fake_http):
    received = []
    fake_http["http://gotify"] = lambda req: received.append(req) or httpx.Response(200)
    n = action_notification("fr", "delete_selection", MATRIX, success=1, failures=1, freed_bytes=None, steps=STEPS)

    asyncio.run(send(Targets(None, None, None, "http://gotify/", "gotify-secret"), n))

    [req] = received
    body = json.loads(req.content)
    assert req.url.path == "/message" and req.headers["X-Gotify-Key"] == "gotify-secret"
    assert body["priority"] == 7 and body["extras"]["client::display"]["contentType"] == "text/markdown"
    assert "**Échecs** : 1" in body["message"] and "- ❌ " in body["message"]


def test_send_errors_never_expose_urls_or_tokens(fake_http):
    fake_http["http://gotify"] = lambda req: httpx.Response(401)
    targets = Targets(None, "https://unreachable/secret-topic", None, "http://gotify", "gotify-secret")

    results = asyncio.run(send(targets, build_test_notification("fr")))

    assert results == {"ntfy": "ConnectError", "gotify": "HTTP 401"}


def test_notify_respects_preferences_and_attaches_the_cached_poster(fake_http, monkeypatch):
    sent = []
    fake_http["https://discord.com"] = lambda req: sent.append(req) or httpx.Response(204)
    monkeypatch.setattr(
        notifications, "read_cached_poster", lambda item_id, tag: (PNG, "image/png") if (item_id, tag) == ("42", "t1") else None
    )
    settings = Settings(id=1, language="en", notify_discord_webhook=WEBHOOK, notify_on_scan=False)

    async def run():
        summary = scan_completed_notification(
            "en", media=1, duplicates=0, orphans=0, reclaimable_bytes=0, matched=0, torrents=0, duration_seconds=3
        )
        notify(settings, "scan_completed", summary)
        notify(settings, "scan_failed", scan_failed_notification("en", "boom"))
        deletion = action_notification("en", "delete_selection", MATRIX, success=1, failures=0, freed_bytes=None, steps=STEPS[:1])
        notify(settings, "delete_selection", deletion, poster=MATRIX)
        await asyncio.gather(*notifications._pending)

    asyncio.run(run())

    by_title = {discord_payload(req)["embeds"][0]["title"]: req for req in sent}
    assert set(by_title) == {"Scan failed", "Deletion completed"}
    assert PNG in by_title["Deletion completed"].content and PNG not in by_title["Scan failed"].content


def test_test_endpoint_only_targets_saved_channels(admin_client, settings, fake_http):
    assert admin_client.post("/api/settings/notifications/test").status_code == 400

    fake_http["https://discord.com"] = lambda req: httpx.Response(204)
    admin_client.put("/api/settings", json={"notify_discord_webhook": WEBHOOK})
    res = admin_client.post("/api/settings/notifications/test")
    assert res.status_code == 200 and res.json() == {"results": {"discord": None}}


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
