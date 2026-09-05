from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    # Naïf volontairement : SQLite ne conserve pas le fuseau horaire, une
    # comparaison entre un datetime aware fraîchement créé et un datetime
    # relu depuis la base (toujours naïf) lève TypeError. Toutes les valeurs
    # ci-dessous restent en UTC, juste sans tzinfo, par cohérence.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(SQLModel, table=True):
    """Ligne unique (id=1) : le seul compte de l'application. Pas de
    multi-utilisateur en V1 — un tableau de bord self-hosted à usage
    personnel n'en a pas besoin, et ça garde l'auth simple à auditer."""

    id: Optional[int] = Field(default=1, primary_key=True)

    username: str
    password_hash: str

    # Verrouillage anti-bruteforce : incrémenté à chaque échec de connexion,
    # remis à zéro à la connexion réussie. locked_until bloque toute
    # tentative (même avec le bon mot de passe) jusqu'à expiration.
    failed_attempts: int = 0
    locked_until: Optional[datetime] = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Session(SQLModel, table=True):
    """Session de connexion, persistée en base (pas en mémoire) : elle doit
    survivre à un redémarrage de conteneur, ce qui arrive très souvent dans
    ce projet à chaque mise à jour d'image. Le cookie envoyé au navigateur
    contient le token en clair ; seul son hash est stocké ici, comme un mot
    de passe — lire la base ne suffit pas à voler une session active."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    token_hash: str

    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    last_seen_at: datetime = Field(default_factory=_utcnow)
