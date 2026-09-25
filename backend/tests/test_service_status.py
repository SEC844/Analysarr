import httpx

from app.models.arr_instance import ArrInstance
from app.services import service_status
from app.services.connection_test import _diag

URL = "/api/services/status"


def fake_services(fake_http) -> list[str]:
    calls: list[str] = []

    def emby(req: httpx.Request) -> httpx.Response:
        calls.append("emby")
        return httpx.Response(200, json={"ProductName": "Emby Server", "ServerName": "Maison", "Version": "4.9"})

    def arr(name: str, status: int):
        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(name)
            return httpx.Response(status, json={"version": "4.0"})

        return handler

    fake_http["http://emby"] = emby
    fake_http["http://sonarr"] = arr("sonarr", 200)
    fake_http["http://radarr"] = arr("radarr", 401)
    fake_http["http://radarr4k"] = arr("radarr4k", 200)
    # qBittorrent non déclaré : injoignable.
    return calls


def test_status_requires_a_session(client):
    assert client.get(URL).status_code == 401


def test_status_lists_configured_services_without_secrets(admin_client, settings, fake_http):
    fake_services(fake_http)
    res = admin_client.get(URL)

    by_service = {s["service"]: s for s in res.json()["services"]}
    assert set(by_service) == {"emby", "sonarr", "radarr", "qbittorrent"}  # cross-seed et Seer non configurés
    assert by_service["emby"]["ok"] and by_service["emby"]["name"] == "Emby" and by_service["sonarr"]["ok"]
    assert not by_service["radarr"]["ok"] and not by_service["qbittorrent"]["ok"]
    for secret in ("emby-key", "sonarr-key", "radarr-key", "secret"):
        assert secret not in res.text


def test_status_is_cached_and_forced_refresh_is_rate_limited(admin_client, settings, fake_http, monkeypatch):
    calls = fake_services(fake_http)
    admin_client.get(URL)
    first = len(calls)

    admin_client.get(URL)
    admin_client.get(f"{URL}?refresh=true")
    assert len(calls) == first

    later = service_status._clock() + service_status.MIN_REFRESH_SECONDS + 1
    monkeypatch.setattr(service_status, "_clock", lambda: later)
    admin_client.get(f"{URL}?refresh=true")
    assert len(calls) == 2 * first


def test_saved_changes_invalidate_the_cache_and_extra_instances_are_checked(admin_client, settings, fake_http, session):
    calls = fake_services(fake_http)
    admin_client.get(URL)
    session.add(ArrInstance(kind="radarr", name="Radarr 4K", url="http://radarr4k", api_key="uhd-key"))
    session.commit()

    res = admin_client.get(URL)
    assert "radarr4k" in calls
    assert {
        k: v for k, v in res.json()["services"][-2].items() if k != "message"
    } == {"service": "radarr", "name": "Radarr 4K", "ok": True}
    assert "uhd-key" not in res.text


def test_public_status_never_triggers_outbound_checks():
    assert service_status.cached_services_status() is None


def test_qbittorrent_diagnostics_never_echo_cookie_values():
    diag = _diag(httpx.Response(403, headers={"set-cookie": "SID=abcdef123; path=/"}, text="Forbidden"))
    assert "abcdef123" not in diag and "SID=…" in diag
