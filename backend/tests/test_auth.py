from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import select

from app.main import app
from app.models.auth import Session as AuthSession
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


def _only_session(session):
    session.expire_all()
    return session.exec(select(AuthSession)).one()


def test_a_session_is_only_written_back_once_a_minute(admin_client, session):
    """Une page de bibliothèque lance des centaines de requêtes : prolonger la
    session à chacune écrivait autant de fois dans SQLite."""
    first = _only_session(session)
    seen, expires = first.last_seen_at, first.expires_at

    for _ in range(3):
        assert admin_client.get("/api/media").status_code == 200

    again = _only_session(session)
    assert (again.last_seen_at, again.expires_at) == (seen, expires)


def test_a_session_is_still_extended_after_the_interval(admin_client, session):
    auth_session = _only_session(session)
    auth_session.last_seen_at -= timedelta(minutes=2)
    auth_session.expires_at -= timedelta(minutes=2)
    session.add(auth_session)
    session.commit()
    before = auth_session.expires_at

    assert admin_client.get("/api/media").status_code == 200

    assert _only_session(session).expires_at > before


def test_an_expired_session_is_refused_even_if_just_seen(admin_client, session):
    """L'expiration est vérifiée à chaque requête, écriture espacée ou non."""
    auth_session = _only_session(session)
    auth_session.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    session.add(auth_session)
    session.commit()

    assert admin_client.get("/api/media").status_code == 401
    assert admin_client.get("/api/auth/me").status_code == 401
