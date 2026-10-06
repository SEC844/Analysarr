"""Temps réel (la norme, sans réglage) : file d'événements, verrou partagé
avec les scans, webhooks Sonarr/Radarr (branchement automatique et
réception), guetteurs du client torrent, du serveur multimédia (dont les
suppressions) et du gestionnaire de demandes, superviseur, scan de nuit."""

import asyncio
import base64
import json
from datetime import UTC, datetime

import httpx
import pytest
from sqlmodel import Session, select

from app.clients.emby import EmbyClient
from app.clients.qbittorrent import QbittorrentClient
from app.clients.torrent_base import fingerprint
from app.clients.transmission import TransmissionClient
from app.database import engine
from app.models.arr_webhook import ArrWebhook
from app.models.media import Media, MediaType, Torrent
from app.services import scan as scan_package
from app.services.events import RESYNC_EVENT, EventBroadcaster, live_events
from app.services.realtime import hub as hub_module
from app.services.realtime import supervisor as supervisor_module
from app.services.realtime import webhooks
from app.services.realtime.hub import RealtimeHub
from app.services.realtime.status import StatusBoard, board
from app.services.realtime.supervisor import RealtimeSupervisor
from app.services.realtime.watchers import (
    MediaServerWatcher,
    RequestsWatcher,
    SeenItems,
    TorrentChanges,
    TorrentWatcher,
    diff_fingerprints,
    media_item_id,
)
from app.services.scheduler import configure_scan_schedule_from, scheduler

# --- Bus d'événements -----------------------------------------------------------------


def test_a_slow_subscriber_gets_a_resync_instead_of_growing_memory():
    async def run():
        broadcaster = EventBroadcaster(maxsize=2)
        queue = broadcaster.subscribe()
        for i in range(5):
            await broadcaster.publish({"type": "media.updated", "media_id": i})
        return [queue.get_nowait() for _ in range(queue.qsize())]

    assert asyncio.run(run()) == [RESYNC_EVENT]


# --- File d'événements ----------------------------------------------------------------


class RecordingHub(RealtimeHub):
    """File dont le traitement est enregistré au lieu d'être exécuté."""

    def __init__(self) -> None:
        super().__init__()
        self.batches: list[tuple[dict[int, bool], list[str]]] = []

    async def process(self, media: dict[int, bool], services: list[str]) -> None:
        self.batches.append((media, services))


def run_hub(hub: RealtimeHub, signals, wait: float) -> None:
    async def run():
        hub.start()
        signals(hub)
        await asyncio.sleep(wait)
        await hub.stop()

    asyncio.run(run())


def test_signals_for_one_media_are_merged_into_one_analysis():
    hub = RecordingHub()
    hub.debounce = 0.05

    def signals(h: RealtimeHub) -> None:
        h.media_changed(7, watch_only=True)
        h.media_changed(7)
        h.media_changed(7, watch_only=True)
        h.media_changed(8, watch_only=True)

    run_hub(hub, signals, 0.2)

    # Une analyse complète l'emporte sur le simple visionnage.
    assert hub.batches == [({7: True, 8: False}, [])]


def test_the_window_starts_at_the_first_signal_and_never_grows():
    hub = RecordingHub()
    hub.debounce = 0.1

    async def run():
        hub.start()
        for _ in range(6):
            hub.media_changed(1)
            await asyncio.sleep(0.03)
        await asyncio.sleep(0.15)
        await hub.stop()

    asyncio.run(run())

    # Signalé sans cesse pendant 0,18 s : analysé à 0,1 s, puis une seconde fois.
    assert len(hub.batches) == 2


def test_a_service_is_not_analysed_again_before_its_cooldown(monkeypatch):
    monkeypatch.setattr(hub_module, "SERVICE_COOLDOWN", 0.3)
    hub = RecordingHub()
    hub.debounce = 0.02

    async def run():
        hub.start()
        hub.service_changed("torrents")
        await asyncio.sleep(0.1)
        hub.service_changed("torrents")
        await asyncio.sleep(0.1)
        early = len(hub.batches)
        await asyncio.sleep(0.3)
        await hub.stop()
        return early

    early = asyncio.run(run())

    assert early == 1  # la seconde demande attend la fin du délai...
    assert [services for _, services in hub.batches] == [["torrents"], ["torrents"]]  # ...sans être perdue


def test_an_unknown_scope_is_refused():
    with pytest.raises(ValueError):
        RealtimeHub().service_changed("tout")


def _events(run):
    async def wrapped():
        queue = live_events.subscribe()
        try:
            await run()
            return [queue.get_nowait() for _ in range(queue.qsize())]
        finally:
            live_events.unsubscribe(queue)

    return asyncio.run(wrapped())


def test_processing_rescans_and_tells_the_browser(session, settings, monkeypatch):
    kept = Media(media_type=MediaType.movie, title="Gardé")
    gone = Media(media_type=MediaType.movie, title="Retiré")
    watched = Media(media_type=MediaType.movie, title="Vu")
    session.add_all([kept, gone, watched])
    session.commit()
    calls = []

    async def rescan(_session, _settings, media):
        calls.append(("rescan", media.title))
        return type("Result", (), {"media_deleted": media.title == "Retiré"})()

    async def refresh_watch(_session, media, _settings):
        calls.append(("watch", media.title))
        return True

    monkeypatch.setattr("app.services.media_rescan.rescan_media", rescan)
    monkeypatch.setattr("app.services.watch_stats.refresh_media_watch", refresh_watch)

    events = _events(lambda: RealtimeHub().process({kept.id: True, gone.id: True, watched.id: False, 999: True}, []))

    assert sorted(calls) == [("rescan", "Gardé"), ("rescan", "Retiré"), ("watch", "Vu")]
    by_type = {(e["type"], e.get("media_id")) for e in events}
    assert ("media.updated", kept.id) in by_type and ("media.removed", gone.id) in by_type
    assert ("media.removed", 999) in by_type  # disparu entre-temps
    assert ("cleanup.changed", None) in by_type


def test_a_failing_source_never_stops_the_batch(session, settings, monkeypatch, caplog):
    first = Media(media_type=MediaType.movie, title="Panne")
    second = Media(media_type=MediaType.movie, title="Sain")
    session.add_all([first, second])
    session.commit()
    seen = []

    async def rescan(_session, _settings, media):
        if media.title == "Panne":
            raise httpx.ConnectError("injoignable")
        seen.append(media.title)
        return type("Result", (), {"media_deleted": False})()

    monkeypatch.setattr("app.services.media_rescan.rescan_media", rescan)

    asyncio.run(RealtimeHub().process({first.id: True, second.id: True}, []))

    assert seen == ["Sain"]
    assert "impossible" in caplog.text


# --- Verrou partagé avec les scans ------------------------------------------------------


def test_a_scan_waits_for_a_short_analysis_instead_of_being_dropped(monkeypatch):
    order = []

    async def fake_scan(trigger, scope):
        order.append("scan")

    monkeypatch.setattr(scan_package, "_run_scan_impl", fake_scan)

    async def run():
        async with scan_package.short_analysis():
            assert not scan_package.is_scan_running()
            scan = asyncio.create_task(scan_package.run_scan("scheduled"))
            await asyncio.sleep(0.01)
            # En attente du verrou : vu comme un scan, un second est refusé.
            assert scan_package.is_scan_running()
            await scan_package.run_scan("scheduled")
            order.append("short")
        await scan
        return order

    assert asyncio.run(run()) == ["short", "scan"]


def test_a_short_analysis_waits_for_a_running_scan(monkeypatch):
    order = []

    async def slow_scan(trigger, scope):
        await asyncio.sleep(0.05)
        order.append("scan")

    monkeypatch.setattr(scan_package, "_run_scan_impl", slow_scan)

    async def run():
        scan = asyncio.create_task(scan_package.run_scan())
        await asyncio.sleep(0.01)
        async with scan_package.short_analysis():
            order.append("short")
        await scan

    asyncio.run(run())
    assert order == ["scan", "short"]


# --- Webhooks : branchement ---------------------------------------------------------------

SCHEMA = [
    {
        "implementation": "Webhook",
        "configContract": "WebhookSettings",
        "onGrab": False,
        "onDownload": False,
        "onUpgrade": False,
        "onSeriesDelete": False,
        "onHealthIssue": False,
        "fields": [
            {"name": "url", "value": ""},
            {"name": "method", "value": 1},
            {"name": "username", "value": ""},
            {"name": "password", "value": ""},
        ],
        "presets": [],
    },
    {"implementation": "Discord", "fields": []},
]


def sonarr_server(calls: list[tuple[str, str, object]], *, existing=(), refuse=False):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/api/v3/notification/schema":
            return httpx.Response(200, json=SCHEMA)
        if path == "/api/v3/notification" and request.method == "GET":
            return httpx.Response(200, json=list(existing))
        if path.startswith("/api/v3/notification") and request.method in ("POST", "PUT") and not path.endswith("/test"):
            # Sonarr teste le webhook AVANT de l'enregistrer : le nouveau
            # secret doit déjà être accepté par Analysarr à cet instant.
            with Session(engine) as db:
                row = db.exec(select(ArrWebhook)).one()
                password = next(f["value"] for f in body["fields"] if f["name"] == "password")
                assert webhooks.secret_matches(row, password)
            if refuse:
                return httpx.Response(400, json=[{"errorMessage": "Unable to send test message"}])
            return httpx.Response(200, json={**body, "id": body.get("id") or 42})
        if path == "/api/v3/notification/42" and request.method == "GET":
            return httpx.Response(200, json={"id": 42, "fields": [{"name": "password", "value": "********"}]})
        if path == "/api/v3/notification/test":
            return httpx.Response(200, json={})
        if path == "/api/v3/notification/42" and request.method == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    return handler


def reconcile(sup: RealtimeSupervisor | None = None, *, retry: bool = True) -> RealtimeSupervisor:
    """Une passe du superviseur sur les webhooks (sans démarrer les guetteurs)."""
    sup = sup or RealtimeSupervisor(RealtimeHub(), board)
    sup.running = True
    asyncio.run(sup._reconcile_webhooks(supervisor_module._load(), retry=retry))
    return sup


def _connect(admin_client, url="http://analysarr:1818", sup: RealtimeSupervisor | None = None) -> dict:
    assert admin_client.put("/api/realtime/address", json={"analysarr_url": url}).status_code == 200
    reconcile(sup)
    return {w["service"]: w for w in admin_client.get("/api/realtime/webhooks").json()["webhooks"]}


@pytest.fixture(autouse=True)
def fresh_board():
    board.clear()
    yield
    board.clear()


def test_every_instance_gets_its_webhook_automatically(admin_client, settings, fake_http):
    calls: list = []
    fake_http["http://sonarr"] = sonarr_server(calls)

    hooks = _connect(admin_client)

    assert hooks["sonarr"]["state"] == "connected"
    assert hooks["sonarr"]["url"] == "http://analysarr:1818/api/webhooks/sonarr/0"
    # Radarr injoignable dans ce test : en erreur, avec la raison.
    assert hooks["radarr"]["state"] == "error" and hooks["radarr"]["error"].startswith("Radarr injoignable")
    [(_, _, body)] = [c for c in calls if c[0] == "POST" and c[1] == "/api/v3/notification"]
    fields = {f["name"]: f["value"] for f in body["fields"]}
    assert fields["url"] == "http://analysarr:1818/api/webhooks/sonarr/0" and fields["username"] == "analysarr"
    assert body["onDownload"] and body["onSeriesDelete"] and not body["onHealthIssue"]
    assert "presets" not in body and "id" not in body
    with Session(engine) as db:
        row = db.exec(select(ArrWebhook)).one()
        assert (row.notification_id, row.pending_secret_hash) == (42, None)
        assert row.target_signature == webhooks.target_signature(webhooks.wanted_webhooks(db, settings)[0].target)
        assert fields["password"] not in (row.secret_hash, row.url)  # seule l'empreinte est gardée
        assert webhooks.secret_matches(row, fields["password"])


def test_an_existing_analysarr_webhook_is_reused_never_duplicated(admin_client, settings, fake_http):
    calls: list = []
    existing = [
        {
            "id": 42,
            "implementation": "Webhook",
            "fields": [{"name": "url", "value": "http://analysarr:1818/api/webhooks/sonarr/0"}],
        }
    ]
    fake_http["http://sonarr"] = sonarr_server(calls, existing=existing)

    assert _connect(admin_client)["sonarr"]["state"] == "connected"

    assert [c[:2] for c in calls if c[0] in ("POST", "PUT")] == [("PUT", "/api/v3/notification/42")]


def test_a_refused_creation_leaves_nothing_behind_and_says_why(admin_client, settings, fake_http):
    fake_http["http://sonarr"] = sonarr_server([], refuse=True)

    hook = _connect(admin_client)["sonarr"]

    assert hook["state"] == "error" and hook["error"].startswith("Sonarr a refusé le webhook")
    assert "Unable to send test message" in hook["error"]
    with Session(engine) as db:
        assert db.exec(select(ArrWebhook)).all() == []


def test_a_failed_attempt_waits_before_trying_again_unless_asked(admin_client, settings, fake_http):
    calls: list = []
    fake_http["http://sonarr"] = sonarr_server(calls, refuse=True)
    sup = RealtimeSupervisor(RealtimeHub(), board)
    _connect(admin_client, sup=sup)
    attempts = len([c for c in calls if c[0] == "POST"])

    reconcile(sup, retry=False)
    assert len([c for c in calls if c[0] == "POST"]) == attempts  # pas avant le délai

    reconcile(sup, retry=True)
    assert len([c for c in calls if c[0] == "POST"]) == attempts + 1


def test_a_new_address_updates_the_webhook_and_a_refusal_keeps_the_working_secret(admin_client, settings, fake_http):
    calls: list = []
    fake_http["http://sonarr"] = sonarr_server(calls)
    _connect(admin_client)
    with Session(engine) as db:
        working = db.exec(select(ArrWebhook)).one().secret_hash

    fake_http["http://sonarr"] = sonarr_server(calls, refuse=True)
    assert _connect(admin_client, "http://analysarr.lan:1818")["sonarr"]["state"] == "error"
    with Session(engine) as db:
        row = db.exec(select(ArrWebhook)).one()
        assert (row.secret_hash, row.pending_secret_hash) == (working, None)

    fake_http["http://sonarr"] = sonarr_server(calls)
    assert _connect(admin_client, "http://analysarr.lan:1818")["sonarr"]["state"] == "connected"
    # Mise à jour du webhook existant chez Sonarr, jamais un second.
    assert ("PUT", "/api/v3/notification/42") in [c[:2] for c in calls]


def test_a_moved_instance_gets_a_new_webhook(admin_client, session, settings, fake_http):
    calls: list = []
    fake_http["http://sonarr"] = sonarr_server(calls)
    _connect(admin_client)
    moved: list = []
    fake_http["http://sonarr2"] = sonarr_server(moved)
    settings.sonarr_url = "http://sonarr2"
    session.add(settings)
    session.commit()

    reconcile()

    # L'ancienne notification (id 42) vivait sur l'autre adresse : création.
    assert [c[:2] for c in moved if c[0] in ("POST", "PUT")] == [("POST", "/api/v3/notification")]


@pytest.mark.parametrize(
    "url", ["ftp://analysarr", "analysarr:1818", "http://user:pass@analysarr", "http://analysarr/?token=1"]
)
def test_an_invalid_analysarr_address_is_refused(admin_client, settings, url):
    assert admin_client.put("/api/realtime/address", json={"analysarr_url": url}).status_code == 422


def test_a_detected_address_never_overwrites_a_saved_one(admin_client, settings):
    put = admin_client.put
    assert put("/api/realtime/address", json={"analysarr_url": "http://a:1818", "detected": True}).status_code == 200
    put("/api/realtime/address", json={"analysarr_url": "http://b:1818", "detected": True})
    assert admin_client.get("/api/realtime/webhooks").json()["analysarr_url"] == "http://a:1818"
    put("/api/realtime/address", json={"analysarr_url": "http://c:1818"})
    assert admin_client.get("/api/realtime/webhooks").json()["analysarr_url"] == "http://c:1818"


def test_without_an_address_nothing_is_attempted(admin_client, settings, fake_http):
    calls: list = []
    fake_http["http://sonarr"] = sonarr_server(calls)

    reconcile()

    hooks = {w["service"]: w for w in admin_client.get("/api/realtime/webhooks").json()["webhooks"]}
    assert hooks["sonarr"]["state"] == "no_address" and calls == []
    assert admin_client.post("/api/realtime/webhooks/sonarr/0").status_code == 409
    assert admin_client.get("/api/realtime/status").json()["address_set"] is False


def test_a_removed_instance_loses_its_row(session, settings):
    session.add(ArrWebhook(service="sonarr", instance_id=7, notification_id=3, secret_hash="x", url="u"))
    session.commit()

    reconcile()

    with Session(engine) as db:
        assert db.exec(select(ArrWebhook)).all() == []


def test_the_test_button_asks_sonarr_to_call_back(admin_client, settings, fake_http):
    calls: list = []
    fake_http["http://sonarr"] = sonarr_server(calls)
    _connect(admin_client)

    response = admin_client.post("/api/realtime/webhooks/sonarr/0/test")

    assert response.status_code == 204
    [(_, _, body)] = [c for c in calls if c[1] == "/api/v3/notification/test"]
    # Mot de passe masqué renvoyé tel quel : Sonarr reprend le sien.
    assert body["fields"][0]["value"] == "********"


def test_removing_an_instance_removes_its_webhook_on_both_sides(admin_client, settings, fake_http):
    calls: list = []
    fake_http["http://sonarr"] = sonarr_server(calls)
    _connect(admin_client)

    response = admin_client.delete("/api/realtime/webhooks/sonarr/0")

    assert response.status_code == 204
    assert ("DELETE", "/api/v3/notification/42", None) in calls
    with Session(engine) as db:
        assert db.exec(select(ArrWebhook)).all() == []
    assert admin_client.delete("/api/realtime/webhooks/sonarr/0").status_code == 204  # déjà parti : sans effet


def test_an_unreachable_service_keeps_the_webhook_listed(admin_client, settings, fake_http):
    fake_http["http://sonarr"] = sonarr_server([])
    _connect(admin_client)
    del fake_http["http://sonarr"]

    assert admin_client.delete("/api/realtime/webhooks/sonarr/0").status_code == 502
    with Session(engine) as db:
        assert len(db.exec(select(ArrWebhook)).all()) == 1


# --- Webhooks : réception ------------------------------------------------------------------


@pytest.fixture
def hooked(session, settings):
    secret = "s3cret-value"
    session.add(
        ArrWebhook(
            service="sonarr",
            instance_id=0,
            notification_id=42,
            secret_hash=webhooks._digest(secret),
            url="http://analysarr/api/webhooks/sonarr/0",
        )
    )
    session.commit()
    board.register(webhooks.source_key("sonarr", 0), "webhook")
    yield secret
    board.clear()


@pytest.fixture
def signals(monkeypatch):
    received: list[tuple[str, object]] = []
    monkeypatch.setattr(
        hub_module.hub, "media_changed", lambda media_id, watch_only=False: received.append(("media", media_id))
    )
    monkeypatch.setattr(hub_module.hub, "service_changed", lambda scope: received.append(("service", scope)))
    return received


def basic(secret: str, user: str = "analysarr") -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()}


def deliver(client, secret, body, path="/api/webhooks/sonarr/0", **headers):
    return client.post(path, content=json.dumps(body), headers={**basic(secret), **headers})


@pytest.mark.parametrize(
    ("path", "auth"),
    [
        ("/api/webhooks/sonarr/0", None),
        ("/api/webhooks/sonarr/0", basic("faux")),
        ("/api/webhooks/sonarr/0", basic("s3cret-value", user="admin")),
        ("/api/webhooks/radarr/0", basic("s3cret-value")),
        ("/api/webhooks/sonarr/3", basic("s3cret-value")),
    ],
)
def test_a_webhook_without_the_right_secret_is_refused_the_same_way(client, hooked, signals, path, auth):
    response = client.post(path, content=json.dumps({"eventType": "Download"}), headers=auth or {})

    assert (response.status_code, response.json()) == (401, {"detail": "Webhook non autorisé."})
    assert signals == []


def test_a_test_event_confirms_the_connection(client, hooked, signals):
    response = deliver(client, hooked, {"eventType": "Test", "series": {"id": 1}})

    assert response.status_code == 204
    assert signals == []
    status = board.get(webhooks.source_key("sonarr", 0))
    assert status is not None and status.state == "active" and status.last_event_at is not None


def test_an_import_rescans_the_known_series_only_by_its_id(client, session, hooked, signals):
    media = Media(media_type=MediaType.series, title="Vraie", sonarr_id=12)
    session.add(media)
    session.commit()

    # La charge ment sur tout le reste : seul l'identifiant sert.
    body = {
        "eventType": "Download",
        "series": {"id": 12, "title": "Autre", "path": "/etc"},
        "episodeFile": {"path": "/x"},
    }
    assert deliver(client, hooked, body).status_code == 204

    assert signals == [("media", media.id)]


def test_an_unknown_series_asks_for_a_sonarr_analysis(client, hooked, signals):
    assert deliver(client, hooked, {"eventType": "SeriesAdd", "series": {"id": 99}}).status_code == 204
    assert signals == [("service", "sonarr")]


def test_irrelevant_events_change_nothing(client, hooked, signals):
    assert deliver(client, hooked, {"eventType": "Health", "series": {"id": 12}}).status_code == 204
    assert signals == []


@pytest.mark.parametrize("raw", ["pas du json", json.dumps([1, 2]), json.dumps({"series": {"id": 1}})])
def test_an_unreadable_payload_is_refused(client, hooked, raw):
    assert client.post("/api/webhooks/sonarr/0", content=raw, headers=basic(hooked)).status_code == 400


def test_an_oversized_payload_is_refused(client, hooked):
    big = json.dumps({"eventType": "Download", "pad": "x" * 70_000})
    assert client.post("/api/webhooks/sonarr/0", content=big, headers=basic(hooked)).status_code == 413


def test_a_burst_is_rate_limited(client, hooked, signals, monkeypatch):
    monkeypatch.setattr("app.routers.webhooks.RATE_LIMIT", 3)
    codes = [deliver(client, hooked, {"eventType": "Test"}).status_code for _ in range(5)]
    assert codes == [204, 204, 204, 429, 429]


def test_the_previous_secret_stays_valid_during_a_reconnection(session, hooked):
    row = session.exec(select(ArrWebhook)).one()
    row.pending_secret_hash = webhooks._digest("nouveau")
    assert webhooks.secret_matches(row, hooked) and webhooks.secret_matches(row, "nouveau")
    assert not webhooks.secret_matches(row, "autre")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"eventType": "Download", "series": {"id": 5}}, webhooks.WebhookEvent("Download", 5)),
        ({"eventType": "Download", "series": {"id": True}}, webhooks.WebhookEvent("Download", None)),
        ({"eventType": "Download", "series": {"id": -3}}, webhooks.WebhookEvent("Download", None)),
        ({"eventType": "Download", "movie": {"id": 5}}, webhooks.WebhookEvent("Download", None)),
        ({"eventType": 3}, None),
        ([], None),
    ],
)
def test_only_the_event_type_and_id_are_read(body, expected):
    assert webhooks.parse_event("sonarr", body) == expected


# --- Client torrent -------------------------------------------------------------------------


def fp(name="T", save="/d", complete=True):
    return fingerprint(name, save, None, None, complete)


def test_only_meaningful_torrent_changes_count():
    previous = {"a": fp(), "b": fp(), "c": fp(complete=False)}
    current = {"a": fp(), "c": fp(complete=True), "d": fp()}

    assert diff_fingerprints(previous, current) == TorrentChanges(changed={"c"}, removed={"b"}, added={"d"})


def test_qbittorrent_rebuilds_its_state_from_partial_updates(fake_http):
    pages = iter(
        [
            {"rid": 1, "full_update": True, "torrents": {"AA": {"name": "A", "save_path": "/d", "progress": 0.5}}},
            # Différentiel : seuls les champs modifiés, plus les torrents retirés.
            {"rid": 2, "torrents": {"AA": {"progress": 1}, "BB": {"name": "B", "progress": 1}}},
            {"rid": 3, "torrents_removed": ["AA"]},
        ]
    )
    rids = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=1; path=/"})
        rids.append(request.url.params["rid"])
        return httpx.Response(200, json=next(pages))

    fake_http["http://qbit"] = handler

    async def run():
        async with QbittorrentClient("http://qbit", "admin", "x") as client:
            return [await client.fingerprints() for _ in range(3)]

    first, second, third = asyncio.run(run())

    assert rids == ["0", "1", "2"]
    assert first == {"aa": fingerprint("A", "/d", None, None, False)}
    assert second["aa"] == fingerprint("A", "/d", None, None, True) and "bb" in second
    assert set(third) == {"bb"}


def test_transmission_reads_only_recently_active_torrents(fake_http):
    calls = []
    replies = iter(
        [
            {"torrents": [{"id": 1, "hashString": "AA", "name": "A", "percentDone": 1}]},
            {"torrents": [{"id": 2, "hashString": "BB", "name": "B", "percentDone": 0.2}], "removed": [1]},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "session-get":
            return httpx.Response(200, json={"result": "success", "arguments": {}})
        calls.append(body["arguments"])
        return httpx.Response(200, json={"result": "success", "arguments": next(replies)})

    fake_http["http://transmission"] = handler

    async def run():
        async with TransmissionClient("http://transmission", None, None) as client:
            return [await client.fingerprints() for _ in range(2)]

    first, second = asyncio.run(run())

    assert "ids" not in calls[0] and calls[1]["ids"] == "recently-active"
    assert set(first) == {"aa"} and set(second) == {"bb"}


def test_the_torrent_watcher_signals_known_media_and_new_finished_torrents(session, settings, monkeypatch):
    media = Media(media_type=MediaType.movie, title="Film")
    session.add(media)
    session.commit()
    session.add(Torrent(media_id=media.id, hash="KNOWN", name="Film"))
    session.commit()
    received = []
    hub = RealtimeHub()
    monkeypatch.setattr(hub, "media_changed", lambda media_id, watch_only=False: received.append(media_id))
    monkeypatch.setattr(hub, "service_changed", received.append)
    watcher = TorrentWatcher(settings, 1, hub, StatusBoard())

    changes = TorrentChanges(changed={"known"}, removed=set(), added={"new", "downloading"})
    current = {"known": fp(), "new": fp(complete=True), "downloading": fp(complete=False)}
    asyncio.run(watcher.dispatch(changes, current))

    assert received == [media.id, "torrents"]


def test_a_torrent_still_downloading_triggers_nothing(settings, monkeypatch):
    received = []
    hub = RealtimeHub()
    monkeypatch.setattr(hub, "service_changed", received.append)
    watcher = TorrentWatcher(settings, 1, hub, StatusBoard())

    asyncio.run(watcher.dispatch(TorrentChanges(set(), set(), {"new"}), {"new": fp(complete=False)}))

    assert received == []


def test_a_torrent_client_error_is_reported_and_retried(settings, monkeypatch):
    status_board = StatusBoard()
    watcher = TorrentWatcher(settings, 0.01, RealtimeHub(), status_board)
    attempts = []

    async def failing_session():
        attempts.append(1)
        raise httpx.ConnectError("refusé")

    monkeypatch.setattr(watcher, "session", failing_session)

    async def run():
        task = asyncio.create_task(watcher.run())
        await asyncio.sleep(0.1)
        task.cancel()

    asyncio.run(run())
    status = status_board.get("torrents")
    assert status is not None and (status.state, status.error) == ("error", "ConnectError")
    assert 2 <= len(attempts) <= 5  # backoff : 0,01 puis 0,02, 0,04...


# --- Serveur multimédia ------------------------------------------------------------------


def test_seen_items_skip_repeats_within_the_overlap():
    seen = SeenItems(ttl=30)
    assert seen.is_new("a", "v1", now=0)
    assert not seen.is_new("a", "v1", now=5)
    assert seen.is_new("a", "v2", now=6)
    assert seen.is_new("b", None, now=0)
    assert not seen.is_new("b", None, now=10)
    assert seen.is_new("b", None, now=41)


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"Type": "Movie", "Id": "m1"}, "m1"),
        ({"Type": "Episode", "Id": "e1", "SeriesId": "s1"}, "s1"),
        ({"Type": "Season", "Id": "x", "SeriesId": "s1"}, "s1"),
        ({"Type": "Episode", "Id": "e1"}, None),
    ],
)
def test_an_episode_is_brought_back_to_its_series(item, expected):
    assert media_item_id(item) == expected


def media_server(kind: str, calls: list[httpx.Request], items: list[dict], user_items: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        params = request.url.params
        if "MinDateLastSavedForUser" in params:
            return httpx.Response(200, json={"Items": user_items})
        if "MinDateLastSaved" in params:
            if kind == "jellyfin":
                assert set(params["Fields"].split(",")) <= {"DateLastSaved", "Etag"}
            return httpx.Response(200, json={"Items": items})
        return httpx.Response(404)

    return handler


@pytest.mark.parametrize("kind", ["emby", "jellyfin"])
def test_the_media_server_watcher_signals_changed_media(session, settings, fake_http, monkeypatch, kind):
    series = Media(media_type=MediaType.series, title="Série", emby_item_id="s1")
    movie = Media(media_type=MediaType.movie, title="Film", emby_item_id="m1")
    session.add_all([series, movie])
    session.commit()
    calls: list[httpx.Request] = []
    items = [{"Type": "Episode", "Id": "e1", "SeriesId": "s1", "DateLastSaved": "t1"}, {"Type": "Movie", "Id": "new"}]
    fake_http[f"http://{kind}"] = media_server(kind, calls, items, [{"Type": "Movie", "Id": "m1", "UserData": {}}])
    from app.models.media import EmbyUser

    session.add(EmbyUser(id="u1", name="Marie"))
    session.commit()
    received = []
    hub = RealtimeHub()
    monkeypatch.setattr(
        hub, "media_changed", lambda media_id, watch_only=False: received.append((media_id, watch_only))
    )
    monkeypatch.setattr(hub, "service_changed", received.append)
    watcher = MediaServerWatcher(settings, 1, hub, StatusBoard())
    emby = EmbyClient(f"http://{kind}", "k", kind)
    since = datetime(2026, 10, 5, 12, tzinfo=UTC)

    async def run():
        await watcher.poll_library(emby, since)
        await watcher.poll_library(emby, since)  # recouvrement : rien de neuf
        await watcher.poll_watch(emby, since)

    asyncio.run(run())

    assert received == [(series.id, False), "media_server", (movie.id, True)]
    library = next(r for r in calls if "MinDateLastSaved" in r.url.params)
    assert library.url.params["MinDateLastSaved"] == "2026-10-05T12:00:00Z"
    user = next(r for r in calls if "MinDateLastSavedForUser" in r.url.params)
    assert (user.url.path, user.url.params.get("userId")) == (
        ("/Items", "u1") if kind == "jellyfin" else ("/Users/u1/Items", None)
    )


def test_a_mass_change_becomes_one_library_analysis(session, settings, fake_http, monkeypatch):
    for i in range(60):
        session.add(Media(media_type=MediaType.movie, title=f"F{i}", emby_item_id=f"m{i}"))
    session.commit()
    items = [{"Type": "Movie", "Id": f"m{i}", "DateLastSaved": "t"} for i in range(60)]
    fake_http["http://emby"] = media_server("emby", [], items, [])
    received = []
    hub = RealtimeHub()
    monkeypatch.setattr(hub, "media_changed", lambda media_id, watch_only=False: received.append(media_id))
    monkeypatch.setattr(hub, "service_changed", received.append)
    watcher = MediaServerWatcher(settings, 1, hub, StatusBoard())

    asyncio.run(watcher.poll_library(EmbyClient("http://emby", "k"), datetime.now(UTC)))

    assert received == ["media_server"]


# --- Superviseur, réglages, planification ------------------------------------------------


def test_the_status_reports_the_sources(admin_client, settings, hooked):
    status = admin_client.get("/api/realtime/status").json()

    assert status["active"] is True and status["address_set"] is False
    assert [s["key"] for s in status["sources"]] == ["webhook:sonarr:0"]


def test_the_supervisor_follows_every_configured_service(session, settings, monkeypatch):
    """Aucun réglage : chaque service configuré est suivi ; un guetteur ne
    redémarre que si SA configuration change."""
    started = []

    async def fake_run(self):
        started.append(self.key)
        await asyncio.sleep(3600)

    monkeypatch.setattr("app.services.realtime.watchers.Watcher.run", fake_run)
    supervisor = RealtimeSupervisor(RecordingHub(), StatusBoard())

    def configure(**fields):
        with Session(engine) as db:
            row = db.get(type(settings), 1)
            for key, value in fields.items():
                setattr(row, key, value)
            db.add(row)
            db.commit()

    async def run():
        await supervisor.apply()  # pas démarré : rien
        assert supervisor.active_keys() == []
        await supervisor.start()
        await asyncio.sleep(0)
        assert supervisor.active_keys() == ["media_server", "torrents"]
        await supervisor.apply()  # rien de changé : pas de redémarrage
        configure(seer_enabled=True, seer_url="http://seer", seer_api_key="k")
        await supervisor.apply()
        await asyncio.sleep(0)
        assert supervisor.active_keys() == ["media_server", "requests", "torrents"]
        configure(qbittorrent_url="http://qbit2")
        await supervisor.apply()
        await asyncio.sleep(0)
        configure(qbittorrent_url=None)
        await supervisor.apply()
        assert supervisor.active_keys() == ["media_server", "requests"]
        await supervisor.stop()
        assert supervisor.active_keys() == []

    asyncio.run(run())
    assert sorted(started) == ["media_server", "requests", "torrents", "torrents"]


def test_a_new_install_scans_once_a_night(session):
    from app.models.settings import Settings

    fresh = Settings(id=1)
    assert (fresh.scan_schedule_enabled, fresh.scan_schedule_mode, fresh.scan_nightly_hour) == (True, "nightly", 4)


def test_an_existing_install_switches_once_to_the_nightly_check(session, settings):
    from app.database import _apply_realtime_default

    settings.realtime_default_applied = False
    settings.scan_schedule_enabled = False
    settings.scan_schedule_mode = "interval"
    session.add(settings)
    session.commit()

    _apply_realtime_default()
    session.refresh(settings)
    assert (settings.scan_schedule_enabled, settings.scan_schedule_mode, settings.realtime_default_applied) == (
        True,
        "nightly",
        True,
    )

    # Une fois seulement : un choix fait ensuite par l'utilisateur est gardé.
    settings.scan_schedule_mode = "interval"
    session.add(settings)
    session.commit()
    _apply_realtime_default()
    session.refresh(settings)
    assert settings.scan_schedule_mode == "interval"


def test_the_nightly_schedule_is_a_cron_job(settings):
    try:
        settings.scan_schedule_enabled = True
        settings.scan_schedule_mode = "nightly"
        settings.scan_nightly_hour = 3
        configure_scan_schedule_from(settings)
        job = scheduler.get_job("periodic_scan")
        assert job is not None and "hour='3'" in str(job.trigger)

        settings.scan_schedule_mode = "interval"
        settings.scan_schedule_interval_minutes = 120
        configure_scan_schedule_from(settings)
        assert "interval" in str(scheduler.get_job("periodic_scan").trigger)
    finally:
        if scheduler.get_job("periodic_scan") is not None:
            scheduler.remove_job("periodic_scan")


def test_an_older_client_never_resets_the_nightly_mode(admin_client, settings, session):
    settings.scan_schedule_mode = "nightly"
    session.add(settings)
    session.commit()
    current = admin_client.get("/api/settings").json()
    payload = {
        "emby_url": current["emby"]["url"] if "emby" in current else "http://emby",
        "scan_schedule_enabled": True,
        "scan_schedule_interval_minutes": 60,
    }

    assert admin_client.put("/api/settings", json=payload).status_code == 200
    try:
        assert admin_client.get("/api/settings").json()["schedule"]["mode"] == "nightly"
    finally:
        if scheduler.get_job("periodic_scan") is not None:
            scheduler.remove_job("periodic_scan")


def test_the_events_stream_opens_immediately(monkeypatch):
    from app import sse
    from app.routers.events import events_stream

    monkeypatch.setattr(sse, "HEARTBEAT_SECONDS", 0.01)

    async def run():
        response = await events_stream()
        chunks = response.body_iterator
        first = await chunks.__anext__()
        ping = await chunks.__anext__()
        await live_events.publish({"type": "media.updated", "media_id": 3})
        event = await chunks.__anext__()
        await chunks.aclose()
        return response, first, ping, event

    response, first, ping, event = asyncio.run(run())

    assert (first, ping) == (": connected\n\n", ": ping\n\n")
    assert json.loads(event.removeprefix("data: ")) == {"type": "media.updated", "media_id": 3}
    assert response.headers["x-accel-buffering"] == "no"
    assert live_events.subscriber_count == 0


def test_the_status_is_neutral_before_configuration(admin_client):
    status = admin_client.get("/api/realtime/status").json()

    assert (status["active"], status["sources"]) == (False, [])


def test_end_to_end_a_finished_torrent_reaches_the_browser_within_seconds(session, settings, fake_http, monkeypatch):
    """Chaîne complète, sans rien simuler entre les maillons : superviseur,
    guetteur qBittorrent (vrai `sync/maindata`), correspondance, file,
    analyse du média, événement envoyé au navigateur."""
    media = Media(media_type=MediaType.movie, title="Film")
    session.add(media)
    session.commit()
    session.add(Torrent(media_id=media.id, hash="ABC", name="Film"))
    session.commit()
    monkeypatch.setattr(supervisor_module, "TORRENT_INTERVAL", 2.0)

    finished = {"done": False}

    def qbit(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=1; path=/"})
        progress = 1 if finished["done"] else 0.4
        return httpx.Response(
            200, json={"rid": 1, "full_update": True, "torrents": {"ABC": {"name": "Film", "progress": progress}}}
        )

    fake_http["http://qbit"] = qbit
    rescanned = []

    async def rescan(_session, _settings, media):
        rescanned.append(media.id)
        return type("Result", (), {"media_deleted": False})()

    monkeypatch.setattr("app.services.media_rescan.rescan_media", rescan)

    async def run():
        queue = live_events.subscribe()
        hub = RealtimeHub()
        hub.debounce = 1.0
        supervisor = RealtimeSupervisor(hub, StatusBoard())
        await supervisor.start()
        try:
            await asyncio.sleep(2.5)  # première lecture : la référence, aucun signal
            assert rescanned == []
            finished["done"] = True
            started = asyncio.get_running_loop().time()
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=10)
                if event["type"] == "media.updated":
                    return event, asyncio.get_running_loop().time() - started
        finally:
            await supervisor.stop()
            live_events.unsubscribe(queue)

    event, elapsed = asyncio.run(run())

    assert event == {"type": "media.updated", "media_id": media.id}
    assert rescanned == [media.id]
    # Intervalle de lecture (2 s) + regroupement (1 s), avec de la marge.
    assert elapsed < 5, f"{elapsed:.1f} s"


# --- Suppressions sur le serveur multimédia, demandes -----------------------------------


def counting_server(counts: list[tuple[int, int, int]], *, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Items/Counts":
            if status != 200:
                return httpx.Response(status)
            movies, series, episodes = counts.pop(0) if len(counts) > 1 else counts[0]
            return httpx.Response(200, json={"MovieCount": movies, "SeriesCount": series, "EpisodeCount": episodes})
        return httpx.Response(200, json={"Items": []})

    return handler


@pytest.mark.parametrize("kind", ["emby", "jellyfin"])
def test_a_removal_from_the_media_server_is_seen_through_its_counts(settings, fake_http, monkeypatch, kind):
    fake_http[f"http://{kind}"] = counting_server([(10, 5, 100), (10, 5, 100), (11, 5, 100), (11, 5, 99)])
    received = []
    hub = RealtimeHub()
    monkeypatch.setattr(hub, "service_changed", received.append)
    watcher = MediaServerWatcher(settings, 1, hub, StatusBoard())
    emby = EmbyClient(f"http://{kind}", "k", kind)

    async def run():
        for _ in range(4):
            await watcher.poll_counts(emby)

    asyncio.run(run())

    # Référence, inchangé, ajout (vu ailleurs), un épisode en moins : une analyse.
    assert received == ["media_server"]


def test_a_server_without_counts_is_simply_not_watched_for_removals(settings, fake_http, monkeypatch):
    fake_http["http://emby"] = counting_server([(1, 1, 1)], status=404)
    received = []
    hub = RealtimeHub()
    monkeypatch.setattr(hub, "service_changed", received.append)
    watcher = MediaServerWatcher(settings, 1, hub, StatusBoard())

    asyncio.run(watcher.poll_counts(EmbyClient("http://emby", "k")))

    assert watcher.counts_supported is False and received == []


def test_a_request_change_asks_for_a_requests_analysis(session, settings, fake_http, monkeypatch):
    settings.seer_enabled, settings.seer_url, settings.seer_api_key = True, "http://seer", "k"
    session.add(settings)
    session.commit()
    pages = [
        {"pageInfo": {"results": 2}, "results": [{"id": 1, "updatedAt": "a", "status": 2}]},
        {"pageInfo": {"results": 2}, "results": [{"id": 1, "updatedAt": "a", "status": 2}]},
        {"pageInfo": {"results": 2}, "results": [{"id": 1, "updatedAt": "b", "status": 3}]},
    ]
    seen: list[httpx.Request] = []

    def seer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=pages.pop(0) if len(pages) > 1 else pages[0])

    fake_http["http://seer"] = seer
    received = []
    hub = RealtimeHub()
    monkeypatch.setattr(hub, "service_changed", received.append)
    watcher = RequestsWatcher(settings, 0, hub, StatusBoard())

    async def run():
        task = asyncio.create_task(watcher.session())
        while len(seen) < 3:
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(run())

    assert received == ["seer"]
    assert seen[0].url.params["sort"] == "modified" and seen[0].url.params["take"] == "20"


def test_ombi_is_watched_through_its_totals(fake_http):
    from app.clients.ombi import OmbiClient

    fake_http["http://ombi"] = lambda request: httpx.Response(200, json=3 if "movie" in request.url.path else 5)

    assert asyncio.run(OmbiClient("http://ombi", "k").requests_fingerprint()) == (3, 5)
