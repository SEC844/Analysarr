"""Double authentification TOTP (RFC 6238), sans dépendance externe :
HMAC-SHA1, 6 chiffres, pas de 30 s — le format attendu par toutes les
applications d'authentification (Aegis, 2FAS, Google Authenticator,
Bitwarden...).

Codes de secours : usage unique, seul leur hash sha256 est stocké (même
principe que les tokens de session, voir security.hash_token)."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

ISSUER = "Analysarr"
PERIOD = 30
DIGITS = 6
# Tolérance d'un pas de temps (±30 s) : décalage d'horloge entre le serveur
# et le téléphone.
WINDOW = 1
RECOVERY_CODE_COUNT = 8


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // PERIOD)


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def verify_code(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """Pas de temps du code s'il est valide, sinon None. Un code d'un pas déjà
    utilisé (`<= last_step`) est refusé : un code intercepté ne peut pas être
    rejoué."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - WINDOW, step + WINDOW + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(_code(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, username: str) -> str:
    label = f"{quote(ISSUER, safe='')}:{quote(username, safe='')}"
    params = urlencode({"secret": secret, "issuer": ISSUER, "algorithm": "SHA1", "digits": DIGITS, "period": PERIOD})
    return f"otpauth://totp/{label}?{params}"


def _normalize_recovery_code(code: str) -> str:
    return code.strip().lower().replace("-", "").replace(" ", "")


def generate_recovery_codes() -> list[str]:
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(5)  # 40 bits
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(_normalize_recovery_code(code).encode("utf-8")).hexdigest()
