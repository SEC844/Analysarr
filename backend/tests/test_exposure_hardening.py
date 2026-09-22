"""Durcissement d'une instance exposée : en-têtes de sécurité, limitation de
débit sur l'authentification, adresse réelle du client et journal des
connexions."""

from types import SimpleNamespace

from sqlmodel import select

from app.models.auth import LoginAttempt
from app.services import rate_limit
from app.services.security import client_ip, parse_trusted_proxies
from tests.conftest import ADMIN


def request_from(peer: str, forwarded: str | None = None):
    headers = {"x-forwarded-for": forwarded} if forwarded else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_security_headers_are_sent_on_every_response(client):
    headers = client.get("/api/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert headers["Referrer-Policy"] == "same-origin"
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "object-src 'none'" in csp and "frame-ancestors 'self'" in csp
    # Aucun script tiers : tout est servi par l'application elle-même.
    assert "script-src 'self';" in csp


def test_unauthenticated_answers_keep_the_headers(client):
    response = client.get("/api/media")
    assert response.status_code == 401
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_forwarded_headers_are_ignored_without_a_trusted_proxy():
    assert client_ip(request_from("10.0.0.9", "1.2.3.4"), []) == "10.0.0.9"
    trusted = parse_trusted_proxies("10.0.0.0/24")
    assert client_ip(request_from("10.0.0.9", "1.2.3.4"), trusted) == "1.2.3.4"
    # Chaîne forgée par le client : seule la dernière adresse hors proxys compte.
    assert client_ip(request_from("10.0.0.9", "9.9.9.9, 1.2.3.4"), trusted) == "1.2.3.4"
    # Adresse illisible dans le réglage : aucune confiance accordée.
    assert client_ip(request_from("10.0.0.9", "1.2.3.4"), parse_trusted_proxies("pas-une-ip")) == "10.0.0.9"


def test_repeated_logins_are_rate_limited(client, session):
    assert client.post("/api/auth/setup", json=ADMIN).status_code == 201
    wrong = {"username": ADMIN["username"], "password": "faux"}

    codes = [client.post("/api/auth/login", json=wrong).status_code for _ in range(rate_limit.MAX_REQUESTS + 2)]
    assert codes[-1] == 429
    # Le compte reste verrouillé de son côté : les deux protections coexistent.
    assert 429 in codes

    limited = client.post("/api/auth/login", json=wrong)
    assert limited.status_code == 429 and limited.headers["Retry-After"]
    assert session.exec(select(LoginAttempt).where(LoginAttempt.reason == "rate_limited")).first() is not None


def test_status_is_never_rate_limited(client):
    for _ in range(rate_limit.MAX_REQUESTS + 5):
        assert client.get("/api/auth/status").status_code == 200


def test_login_history_records_success_and_failure(admin_client, session):
    admin_client.post("/api/auth/login", json={"username": ADMIN["username"], "password": "faux"})

    history = admin_client.get("/api/auth/login-history").json()
    assert [row["success"] for row in history] == [False, True]
    assert history[0]["reason"] == "password"
    assert all(row["ip"] for row in history)


def test_login_history_needs_a_session(client):
    assert client.post("/api/auth/setup", json=ADMIN).status_code == 201
    assert client.get("/api/auth/login-history").status_code == 401


def test_trusted_proxies_are_validated_and_protected(admin_client, session):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models.settings import Settings

    with TestClient(app) as anonymous:  # sans cookie de session
        assert anonymous.get("/api/auth/security").status_code == 401
    assert admin_client.put("/api/auth/security", json={"trusted_proxies": "10.0.0.0/24, 192.168.1.5"}).status_code == 200
    session.expire_all()
    assert session.get(Settings, 1).trusted_proxies == "10.0.0.0/24, 192.168.1.5"

    refused = admin_client.put("/api/auth/security", json={"trusted_proxies": "10.0.0.0/24, pas-une-ip"})
    assert refused.status_code == 400
    session.expire_all()
    assert session.get(Settings, 1).trusted_proxies == "10.0.0.0/24, 192.168.1.5"  # inchangé
