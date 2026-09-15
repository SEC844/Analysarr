import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.models.media import EmbyUser
from app.services import updates
from app.services.poster_cache import safe_image_type


def github_release(tag: str, html_url: str):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/releases/latest")
        return httpx.Response(200, json={"tag_name": tag, "html_url": html_url, "published_at": "2026-09-15T10:00:00Z"})

    return handler


@pytest.mark.parametrize(("value", "expected"), [("v1.2.3", (1, 2, 3)), ("0.18.0", (0, 18, 0)), ("dev", None), ("1.2", None)])
def test_parse_version(value, expected):
    assert updates.parse_version(value) == expected


def test_update_available_only_for_a_newer_release(fake_http, monkeypatch):
    fake_http["https://api.github.com"] = github_release("v0.19.0", f"{updates.RELEASES_PAGE}/tag/v0.19.0")
    monkeypatch.setattr(updates, "APP_VERSION", "0.18.0")
    assert asyncio.run(updates.get_update_status()).update_available

    updates._cached = None
    monkeypatch.setattr(updates, "APP_VERSION", "dev")
    assert not asyncio.run(updates.get_update_status(force=True)).update_available


def test_release_link_outside_the_official_repository_is_never_relayed(fake_http):
    fake_http["https://api.github.com"] = github_release("v9.0.0", "https://evil.example/phishing")
    assert asyncio.run(updates.get_update_status()).release_url == updates.RELEASES_PAGE


def test_forced_checks_are_rate_limited(fake_http):
    calls = []

    def handler(request):
        calls.append(request)
        return github_release("v1.0.0", f"{updates.RELEASES_PAGE}/tag/v1.0.0")(request)

    fake_http["https://api.github.com"] = handler
    asyncio.run(updates.get_update_status(force=True))
    asyncio.run(updates.get_update_status(force=True))
    assert len(calls) == 1


def test_ui_preferences_are_validated_and_kept(admin_client):
    info = admin_client.get("/api/app/info").json()
    assert info["ui"]["library_default_sort"] == "title"

    ui = info["ui"] | {"card_show_total_size": True, "library_default_sort": "cleanup"}
    assert admin_client.put("/api/app/preferences", json={"language": "en", "update_check_enabled": False, "ui": ui}).status_code == 200
    # Sans `ui`, les préférences d'affichage restent inchangées.
    kept = admin_client.put("/api/app/preferences", json={"language": "fr", "update_check_enabled": False}).json()
    assert kept["ui"]["card_show_total_size"] and kept["ui"]["library_default_sort"] == "cleanup"

    bad = ui | {"library_default_sort": "'; DROP TABLE media; --"}
    assert admin_client.put("/api/app/preferences", json={"language": "fr", "update_check_enabled": False, "ui": bad}).status_code == 422
    assert admin_client.put("/api/app/preferences", json={"language": "de", "update_check_enabled": False}).status_code == 422


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [("image/jpeg", "image/jpeg"), ("image/png; charset=binary", "image/png"), ("image/svg+xml", None), ("text/html", None), (None, None)],
)
def test_only_raster_images_are_served(content_type, expected):
    assert safe_image_type(content_type) == expected


def test_avatar_requires_a_valid_and_known_user_id(admin_client, session):
    session.add(EmbyUser(id="abc123", name="Marie", image_tag=None))
    session.commit()
    assert admin_client.get("/api/emby/users/..%2F..%2Fetc%2Fpasswd/avatar").status_code == 404
    assert admin_client.get("/api/emby/users/unknown/avatar").status_code == 404
    assert admin_client.get("/api/emby/users/abc123/avatar").status_code == 404  # pas d'avatar


def test_pages_are_revalidated_but_api_responses_untouched(client):
    assert client.get("/some/page").headers.get("cache-control") == "no-cache"
    assert "cache-control" not in client.get("/api/health").headers


def test_as_utc_keeps_naive_database_dates_in_utc():
    from app.services.watch_stats import as_utc

    assert as_utc(datetime(2026, 1, 1)).tzinfo == timezone.utc
