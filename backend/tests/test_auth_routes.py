"""Quelles routes exigent une session : liste FIGÉE.

Le middleware d'authentification (main.py) décide route par route. Toute
modification de ce middleware — y compris pour des raisons de performance —
doit laisser exactement les mêmes routes publiques, et vérifier la session sur
toutes les autres. Une route ajoutée plus tard est protégée d'office ; en
rendre une publique impose de modifier cette liste, donc de le décider."""

import re

import pytest

from app import main
from app.main import app

# Accessibles sans session. `/api/status` (widget) a sa propre clé API, et les
# routes d'authentification vérifient elles-mêmes ce qui doit l'être (`/me`,
# changement de mot de passe...).
EXPECTED_PUBLIC = {
    "/api/health",
    "/api/status",
    "/api/auth/status",
    "/api/auth/setup",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/auth/password",
    "/api/auth/username",
    "/api/auth/2fa/setup",
    "/api/auth/2fa/enable",
    "/api/auth/2fa/disable",
}


def _api_operations() -> list[tuple[str, str]]:
    """(méthode, chemin) de chaque route d'API, identifiants remplacés par 1."""
    operations = [("GET", "/api/health")]  # hors schéma OpenAPI
    for path, methods in app.openapi()["paths"].items():
        concrete = re.sub(r"\{[^}]+\}", "1", path)
        operations += [(method.upper(), concrete) for method in methods]
    return operations


def test_exactly_the_expected_routes_skip_the_session_check(client, monkeypatch):
    checked: set[str] = set()

    def refuse(request):
        checked.add(request.url.path)
        return False

    monkeypatch.setattr(main, "is_request_authenticated", refuse)
    operations = _api_operations()
    for method, path in operations:
        client.request(method, path)

    all_paths = {path for _, path in operations}
    assert all_paths - checked == EXPECTED_PUBLIC
    assert all_paths >= EXPECTED_PUBLIC  # la liste ne désigne que des routes réelles


@pytest.mark.parametrize(
    "path", ["/api/media", "/api/scan/history", "/api/ignores", "/api/auth/login-history", "/api/library/history"]
)
def test_a_protected_route_refuses_a_request_without_cookie(client, path):
    response = client.get(path)

    assert response.status_code == 401
    assert response.json() == {"detail": "Non authentifié."}


def test_public_routes_stay_reachable_without_cookie(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/auth/status").status_code == 200


def test_a_forged_cookie_is_refused(client):
    client.cookies.set("analysarr_session", "faux-jeton")

    assert client.get("/api/media").status_code == 401
