"""Journal des connexions au compte administrateur.

Sert à repérer ce qu'aucune autre page ne montre : des tentatives répétées
depuis une adresse inconnue, ou une connexion réussie que l'utilisateur ne
reconnaît pas. Borné, comme l'historique des actions."""

from sqlmodel import Session, select

from app.models.auth import LoginAttempt

MAX_ENTRIES = 100
_MAX_USERNAME = 64


def record_attempt(session: Session, *, username: str, ip: str, success: bool, reason: str | None = None) -> None:
    session.add(
        LoginAttempt(
            username=(username or "")[:_MAX_USERNAME],
            ip=ip[:45],  # longueur maximale d'une IPv6 textuelle
            success=success,
            reason=reason,
        )
    )
    session.commit()
    _trim(session)


def _trim(session: Session) -> None:
    ids = session.exec(select(LoginAttempt.id).order_by(LoginAttempt.id.desc()).offset(MAX_ENTRIES)).all()
    if not ids:
        return
    for row in session.exec(select(LoginAttempt).where(LoginAttempt.id.in_(ids))).all():
        session.delete(row)
    session.commit()


def recent_attempts(session: Session, limit: int = 20) -> list[LoginAttempt]:
    return list(session.exec(select(LoginAttempt).order_by(LoginAttempt.id.desc()).limit(limit)).all())
