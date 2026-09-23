from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    # Naïf volontairement : SQLite ne conserve pas le fuseau horaire, une
    # comparaison entre un datetime aware fraîchement créé et un datetime
    # relu depuis la base (toujours naïf) lève TypeError. Toutes les valeurs
    # ci-dessous restent en UTC, juste sans tzinfo, par cohérence.
    return datetime.now(UTC).replace(tzinfo=None)


class User(SQLModel, table=True):
    """Ligne unique (id=1) : le seul compte de l'application. Pas de
    multi-utilisateur en V1 — un tableau de bord self-hosted à usage
    personnel n'en a pas besoin, et ça garde l'auth simple à auditer."""

    id: int | None = Field(default=1, primary_key=True)

    username: str
    password_hash: str

    # Verrouillage anti-bruteforce : incrémenté à chaque échec de connexion,
    # remis à zéro à la connexion réussie. locked_until bloque toute
    # tentative (même avec le bon mot de passe) jusqu'à expiration.
    failed_attempts: int = 0
    locked_until: datetime | None = None

    # Double authentification (TOTP, services/totp.py). `totp_pending_secret` :
    # secret généré mais pas encore confirmé par un premier code valide.
    totp_secret: str | None = None
    totp_pending_secret: str | None = None
    # Dernier pas de temps accepté : un code déjà utilisé ne peut pas être rejoué.
    totp_last_step: int | None = None
    # Codes de secours à usage unique (hash sha256), liste JSON.
    recovery_codes: str = "[]"

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Session(SQLModel, table=True):
    """Session de connexion, persistée en base (pas en mémoire) : elle doit
    survivre à un redémarrage de conteneur, ce qui arrive très souvent dans
    ce projet à chaque mise à jour d'image. Le cookie envoyé au navigateur
    contient le token en clair ; seul son hash est stocké ici, comme un mot
    de passe — lire la base ne suffit pas à voler une session active."""

    id: int | None = Field(default=None, primary_key=True)
    user_id: int
    token_hash: str

    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    last_seen_at: datetime = Field(default_factory=_utcnow)


class LoginAttempt(SQLModel, table=True):
    """Tentative de connexion (réussie ou non) au compte administrateur.
    Table bornée, voir services/login_log.py."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow, index=True)

    username: str = ""
    # Adresse vue par l'application : celle du proxy tant qu'aucun proxy de
    # confiance n'est déclaré (voir services/security.py::client_ip).
    ip: str = ""
    success: bool = False
    # password | otp | locked | rate_limited | unknown_user
    reason: str | None = None
