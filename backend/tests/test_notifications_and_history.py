import asyncio
import json

import httpx

from app.models.activity import ActionLog
from app.models.settings import Settings
from app.schemas.media import DeleteStepResult
from app.services import action_log, notifications
from app.services.action_log import MediaRef, record_action
from app.services.notifications import Targets, notify, send

WEBHOOK = "https://discord.com/api/webhooks/123/secret-token"


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


def test_each_channel_receives_its_own_payload(fake_http):
    received = {}

    def capture(name):
        def handler(req: httpx.Request) -> httpx.Response:
            received[name] = req
            return httpx.Response(200)

        return handler

    fake_http["https://discord.com"] = capture("discord")
    fake_http["https://ntfy.sh"] = capture("ntfy")
    fake_http["http://gotify"] = capture("gotify")
    targets = Targets(WEBHOOK, "https://ntfy.sh/topic", "ntfy-secret", "http://gotify/", "gotify-secret")

    results = asyncio.run(send(targets, "Scan terminé", "3 médias", failed=True))

    assert results == {"discord": None, "ntfy": None, "gotify": None}
    assert json.loads(received["discord"].content)["embeds"][0]["title"] == "Scan terminé"
    assert received["ntfy"].headers["Authorization"] == "Bearer ntfy-secret"
    assert received["ntfy"].url.params["title"] == "Scan terminé" and received["ntfy"].url.params["priority"] == "high"
    assert received["gotify"].url.path == "/message" and received["gotify"].headers["X-Gotify-Key"] == "gotify-secret"


def test_send_errors_never_expose_urls_or_tokens(fake_http):
    fake_http["http://gotify"] = lambda req: httpx.Response(401)
    targets = Targets(None, "https://unreachable/secret-topic", None, "http://gotify", "gotify-secret")

    results = asyncio.run(send(targets, "t", "m"))

    assert results == {"ntfy": "ConnectError", "gotify": "HTTP 401"}


def test_notify_respects_event_preferences_and_language(fake_http):
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content)["embeds"][0])
        return httpx.Response(204)

    fake_http["https://discord.com"] = handler
    settings = Settings(id=1, language="en", notify_discord_webhook=WEBHOOK, notify_on_scan=False)

    async def run():
        notify(settings, "scan_completed", media=1, duplicates=0, orphans=0)
        notify(settings, "scan_failed", failed=True, error="boom")
        notify(settings, "delete_selection", title="Matrix", success=2, failures=1)
        await asyncio.gather(*notifications._pending)

    asyncio.run(run())

    assert [(e["title"], e["description"]) for e in sent] == [
        ("Scan failed", "boom"),
        ("Deletion completed", "Matrix: 2 item(s) deleted · 1 failed"),
    ]


def test_test_endpoint_only_targets_saved_channels(admin_client, settings, fake_http):
    assert admin_client.post("/api/settings/notifications/test").status_code == 400

    fake_http["https://discord.com"] = lambda req: httpx.Response(204)
    admin_client.put("/api/settings", json={"notify_discord_webhook": WEBHOOK})
    res = admin_client.post("/api/settings/notifications/test")
    assert res.status_code == 200 and res.json() == {"results": {"discord": None}}


def test_history_records_actions_and_is_bounded(admin_client, session, monkeypatch):
    monkeypatch.setattr(action_log, "MAX_ENTRIES", 3)
    steps = [DeleteStepResult(kind="torrent", label="Matrix.mkv", success=True), DeleteStepResult(kind="file", label="x", success=False, error="refusé")]
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
