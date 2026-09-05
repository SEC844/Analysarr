import hashlib
import secrets

import bcrypt

SESSION_COOKIE_NAME = "analysarr_session"
SESSION_DURATION_DAYS = 7
LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 15


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """sha256 suffit ici : contrairement à un mot de passe, un token de
    session a une entropie totale (256 bits générés par `secrets`), pas de
    dictionnaire d'attaque possible — pas besoin d'un hash volontairement
    lent comme bcrypt."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
