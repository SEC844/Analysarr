import asyncio
from datetime import datetime, timedelta, timezone

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


def test_page_open_refreshes_in_background_without_blocking(fake_http, monkeypatch):
    """La vérification suit l'ouverture des pages : le premier appel attend le
    résultat, les suivants renvoient le cache et rafraîchissent en fond."""
    calls = []

    def handler(request):
        calls.append(request)
        return github_release(f"v0.19.{len(calls)}", f"{updates.RELEASES_PAGE}/tag/v0.19.0")(request)

    fake_http["https://api.github.com"] = handler
    monkeypatch.setattr(updates, "APP_VERSION", "0.18.0")

    async def scenario():
        first = await updates.status_for_page(True)
        assert first.latest_version == "0.19.1" and len(calls) == 1
        assert await updates.status_for_page(True) is first and len(calls) == 1  # cache frais

        updates._expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert await updates.status_for_page(True) is first and len(calls) == 1  # réponse immédiate
        await updates._refresh_task  # le rafraîchissement, lui, s'est bien lancé
        assert len(calls) == 2
        assert (await updates.status_for_page(True)).latest_version == "0.19.2"

    asyncio.run(scenario())


def test_disabled_update_check_makes_no_outbound_request(fake_http, admin_client):
    def forbidden(request):
        raise AssertionError("aucune requête sortante quand la vérification est désactivée")

    fake_http["https://api.github.com"] = forbidden
    assert admin_client.put("/api/app/preferences", json={"language": "fr", "update_check_enabled": False}).status_code == 200
    assert admin_client.get("/api/app/info").json()["update"] is None


def test_star_prompt_state_is_stored_and_bounded(admin_client):
    """Invitation à mettre une étoile : l'état vit dans les préférences, donc
    le rappel ne revient pas à chaque navigateur ni à chaque redémarrage."""
    info = admin_client.get("/api/app/info").json()
    assert info["ui"]["star_prompt_state"] == "pending" and info["ui"]["star_prompt_at"] is None

    ui = info["ui"] | {"star_prompt_state": "done", "star_prompt_at": "2026-09-22T06:00:00Z"}
    saved = admin_client.put("/api/app/preferences", json={"language": "fr", "update_check_enabled": False, "ui": ui})
    assert saved.json()["ui"]["star_prompt_state"] == "done"

    bad = info["ui"] | {"star_prompt_state": "whatever"}
    refused = admin_client.put("/api/app/preferences", json={"language": "fr", "update_check_enabled": False, "ui": bad})
    assert refused.status_code == 422
