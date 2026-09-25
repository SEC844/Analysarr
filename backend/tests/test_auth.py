from fastapi.testclient import TestClient

from app.main import app
from app.services.security import LOCKOUT_THRESHOLD, SESSION_COOKIE_NAME
from tests.conftest import ADMIN, login


def test_admin_account_can_only_be_created_once(client):
    assert client.post("/api/auth/setup", json=ADMIN).status_code == 201
    assert (
        client.post("/api/auth/setup", json={"username": "intrus", "password": "another-password"}).status_code == 403
    )


def test_setup_rejects_short_password(client):
    assert client.post("/api/auth/setup", json={"username": "admin", "password": "short"}).status_code == 400


def test_api_requires_a_session_except_public_routes(client):
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/media").status_code == 401
    assert client.get("/api/app/info").status_code == 401
    assert client.get("/api/emby/users").status_code == 401
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/status").status_code == 200


def test_login_sets_httponly_session_cookie(client):
    client.post("/api/auth/setup", json=ADMIN)
    response = login(client)
    cookie = response.headers["set-cookie"]
    assert response.status_code == 200
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "Secure" not in cookie  # accès direct en HTTP doit rester possible
    assert client.get("/api/settings").status_code == 200


def test_cookie_is_secure_behind_https_reverse_proxy(client):
    client.post("/api/auth/setup", json=ADMIN)
    response = login(client, headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in response.headers["set-cookie"]


def test_unknown_user_and_wrong_password_share_the_same_error(client):
    client.post("/api/auth/setup", json=ADMIN)
    unknown = client.post("/api/auth/login", json={"username": "nobody", "password": ADMIN["password"]})
    wrong = client.post("/api/auth/login", json={"username": ADMIN["username"], "password": "wrong-password"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_account_is_locked_after_repeated_failures(client):
    client.post("/api/auth/setup", json=ADMIN)
    for _ in range(LOCKOUT_THRESHOLD):
        assert client.post("/api/auth/login", json={**ADMIN, "password": "wrong-password"}).status_code == 401
    # Même le bon mot de passe est refusé pendant le verrouillage.
    assert login(client).status_code == 429


def test_logout_invalidates_the_session_server_side(admin_client):
    token = admin_client.cookies.get(SESSION_COOKIE_NAME)
    assert admin_client.post("/api/auth/logout").status_code == 204
    replay = TestClient(app, cookies={SESSION_COOKIE_NAME: token})
    assert replay.get("/api/settings").status_code == 401


def test_password_change_revokes_other_sessions(admin_client):
    other = TestClient(app)
    assert login(other).status_code == 200

    response = admin_client.put(
        "/api/auth/password", json={"current_password": ADMIN["password"], "new_password": "brand-new-password"}
    )
    assert response.status_code == 200
    assert other.get("/api/settings").status_code == 401
    assert admin_client.get("/api/settings").status_code == 200


def test_settings_never_expose_secrets(admin_client, settings):
    body = admin_client.get("/api/settings").text
    for secret in ("emby-key", "sonarr-key", "radarr-key", "secret"):
        assert secret not in body
