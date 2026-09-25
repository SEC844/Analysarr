from fastapi.testclient import TestClient

from app.main import app
from app.services import totp
from tests.conftest import ADMIN, login


def enable_two_factor(client: TestClient) -> tuple[str, str, list[str]]:
    """Active la 2FA ; renvoie (secret, code utilisé pour l'activation, codes de secours)."""
    setup = client.post("/api/auth/2fa/setup", json={"password": ADMIN["password"]})
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/Analysarr:admin?secret=")
    code = totp._code(secret, totp.current_step())
    enabled = client.post("/api/auth/2fa/enable", json={"code": code})
    assert enabled.status_code == 200
    return secret, code, enabled.json()["codes"]


def test_totp_matches_rfc_6238_reference_values():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
    assert totp._code(secret, 59 // 30) == "287082"
    assert totp._code(secret, 1111111109 // 30) == "081804"


def test_codes_tolerate_clock_drift_but_are_never_replayed():
    secret = totp.generate_secret()
    now = 1_700_000_000
    step = totp.verify_code(secret, totp._code(secret, now // 30 - 1), None, now=now)
    assert step == now // 30 - 1
    assert totp.verify_code(secret, totp._code(secret, step), step, now=now) is None
    assert totp.verify_code(secret, totp._code(secret, now // 30 + 5), None, now=now) is None
    assert totp.verify_code(secret, "12ab56", None, now=now) is None


def test_setup_requires_the_password(admin_client):
    assert admin_client.post("/api/auth/2fa/setup", json={"password": "wrong-password"}).status_code == 401
    assert admin_client.get("/api/auth/me").json()["two_factor_enabled"] is False


def test_two_factor_is_only_enabled_after_a_valid_code(admin_client):
    admin_client.post("/api/auth/2fa/setup", json={"password": ADMIN["password"]})
    assert admin_client.post("/api/auth/2fa/enable", json={"code": "abcdef"}).status_code == 400
    assert admin_client.get("/api/auth/me").json()["two_factor_enabled"] is False
    assert login(TestClient(app)).status_code == 200


def test_login_requires_the_second_factor(admin_client):
    _, activation_code, codes = enable_two_factor(admin_client)
    assert len(set(codes)) == 8
    assert admin_client.get("/api/auth/me").json()["two_factor_enabled"] is True

    other = TestClient(app)
    missing = login(other)
    assert missing.status_code == 401 and missing.json()["two_factor_required"] is True
    assert other.get("/api/settings").status_code == 401

    # Le code déjà utilisé pour l'activation ne peut pas être rejoué.
    replay = other.post("/api/auth/login", json={**ADMIN, "otp": activation_code})
    assert replay.status_code == 401 and replay.json()["two_factor_required"] is True

    # Code de secours : accepté une seule fois.
    assert other.post("/api/auth/login", json={**ADMIN, "otp": codes[0].upper()}).status_code == 200
    assert TestClient(app).post("/api/auth/login", json={**ADMIN, "otp": codes[0]}).status_code == 401


def test_enabling_two_factor_revokes_other_sessions(admin_client):
    other = TestClient(app)
    assert login(other).status_code == 200
    enable_two_factor(admin_client)
    assert other.get("/api/settings").status_code == 401
    assert admin_client.get("/api/settings").status_code == 200


def test_disabling_requires_password_and_a_valid_code(admin_client):
    _, _, codes = enable_two_factor(admin_client)
    disable = "/api/auth/2fa/disable"
    assert admin_client.post(disable, json={"password": "wrong-password", "code": codes[0]}).status_code == 401
    assert admin_client.post(disable, json={"password": ADMIN["password"], "code": "000000-x"}).status_code == 401
    assert admin_client.post(disable, json={"password": ADMIN["password"], "code": codes[1]}).status_code == 200
    assert login(TestClient(app)).status_code == 200


def test_setup_never_reveals_an_active_secret(admin_client):
    enable_two_factor(admin_client)
    assert admin_client.post("/api/auth/2fa/setup", json={"password": ADMIN["password"]}).status_code == 409


def test_username_change_requires_the_password(admin_client):
    url = "/api/auth/username"
    assert admin_client.put(url, json={"username": "new-admin", "password": "wrong-password"}).status_code == 401
    assert admin_client.put(url, json={"username": "   ", "password": ADMIN["password"]}).status_code == 400
    assert admin_client.put(url, json={"username": "x" * 65, "password": ADMIN["password"]}).status_code == 400

    renamed = admin_client.put(url, json={"username": " new-admin ", "password": ADMIN["password"]})
    assert renamed.status_code == 200 and renamed.json()["username"] == "new-admin"
    assert login(TestClient(app)).status_code == 401
    assert (
        TestClient(app)
        .post("/api/auth/login", json={"username": "new-admin", "password": ADMIN["password"]})
        .status_code
        == 200
    )
