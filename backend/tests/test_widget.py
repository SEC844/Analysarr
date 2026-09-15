from fastapi.testclient import TestClient

from app.main import app
from app.models.media import Media, MediaType, ScanRun, ScanStatus
from app.models.settings import Settings

STATUS = "/api/status"
KEY = "/api/settings/widget-key"


def widget(headers: dict[str, str] | None = None, url: str = STATUS):
    return TestClient(app).get(url, headers=headers or {})


def test_widget_is_disabled_by_default(admin_client, settings):
    assert admin_client.get(KEY).json() == {"enabled": False, "key": None}
    assert widget({"X-Api-Key": "anything"}).status_code == 401


def test_key_management_requires_a_session(client, settings):
    assert client.post(KEY).status_code == 401
    assert client.delete(KEY).status_code == 401


def test_generated_key_is_shown_once_and_stored_hashed(admin_client, settings, session):
    key = admin_client.post(KEY).json()["key"]
    assert key.startswith("anl_") and len(key) > 40

    assert admin_client.get(KEY).json() == {"enabled": True, "key": None}
    assert key not in admin_client.get("/api/settings").text
    session.expire_all()  # ligne créée par la fixture : relire la valeur écrite par l'API
    stored = session.get(Settings, 1).widget_api_key_hash
    assert stored and key not in stored


def test_widget_accepts_the_key_in_headers_only(admin_client, settings):
    key = admin_client.post(KEY).json()["key"]
    assert widget({"X-Api-Key": key}).status_code == 200
    assert widget({"Authorization": f"Bearer {key}"}).status_code == 200
    assert widget(url=f"{STATUS}?apikey={key}").status_code == 401
    assert widget({"X-Api-Key": key + "x"}).status_code == 401
    assert widget().status_code == 401


def test_widget_exposes_counts_only(admin_client, settings, session):
    session.add(Media(media_type=MediaType.movie, title="Titre confidentiel", statuses="doublon", reclaimable_bytes=10, total_size=100))
    session.add(Media(media_type=MediaType.series, title="Autre série", total_size=50))
    session.add(ScanRun(status=ScanStatus.completed))
    session.commit()
    key = admin_client.post(KEY).json()["key"]

    res = widget({"X-Api-Key": key})
    body = res.json()
    assert body["media"] == {"total": 2, "movies": 1, "series": 1, "healthy": 1}
    assert body["statuses"]["doublon"] == 1 and body["reclaimable_bytes"] == 10 and body["total_size_bytes"] == 150
    assert body["last_scan"]["status"] == "completed"
    assert body["services"] is None  # aucune vérification récente : jamais déclenchée par le widget
    assert res.headers["cache-control"] == "no-store"
    for private in ("Titre confidentiel", "Autre série", "http://", "emby-key"):
        assert private not in res.text


def test_widget_reports_the_cached_service_status(admin_client, settings):
    key = admin_client.post(KEY).json()["key"]
    admin_client.get("/api/services/status")  # tous injoignables dans les tests
    assert widget({"X-Api-Key": key}).json()["services"] == {"ok": 0, "total": 4}


def test_regenerating_or_revoking_invalidates_the_previous_key(admin_client, settings):
    old = admin_client.post(KEY).json()["key"]
    new = admin_client.post(KEY).json()["key"]
    assert widget({"X-Api-Key": old}).status_code == 401
    assert widget({"X-Api-Key": new}).status_code == 200

    assert admin_client.delete(KEY).json() == {"enabled": False, "key": None}
    assert widget({"X-Api-Key": new}).status_code == 401
