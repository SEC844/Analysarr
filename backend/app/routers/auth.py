from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import Session as DbSession
from sqlmodel import select

from app.database import get_session
from app.models.auth import Session as AuthSession
from app.models.auth import User
from app.schemas.auth import AuthStatus, ChangePasswordRequest, CurrentUser, LoginRequest, SetupRequest
from app.services.security import (
    LOCKOUT_MINUTES,
    LOCKOUT_THRESHOLD,
    SESSION_COOKIE_NAME,
    SESSION_DURATION_DAYS,
    generate_token,
    hash_password,
    hash_token,
    verify_password,
)

router = APIRouter()


def _utcnow() -> datetime:
    # Naïf volontairement : cohérent avec app.models.auth._utcnow (voir son
    # commentaire) — SQLite relit toujours les datetimes sans tzinfo.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cookie_is_secure(request: Request) -> bool:
    """Le cookie `Secure` n'est posé que si la requête est vue en HTTPS —
    jamais en dur, sinon la connexion casse pour un accès direct en HTTP
    (ex: http://10.0.20.110:8000, sans reverse-proxy TLS devant)."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


def _touch_session(token: str, session: DbSession) -> User | None:
    """Résout un token de cookie en utilisateur si la session est valide, et
    prolonge son expiration (session glissante). Utilisé à la fois par la
    dépendance FastAPI `get_current_user` et par le middleware global dans
    main.py — un seul et même chemin de vérification."""
    auth_session = session.exec(select(AuthSession).where(AuthSession.token_hash == hash_token(token))).first()
    if auth_session is None or auth_session.expires_at < _utcnow():
        return None
    user = session.get(User, auth_session.user_id)
    if user is None:
        return None

    auth_session.last_seen_at = _utcnow()
    auth_session.expires_at = _utcnow() + timedelta(days=SESSION_DURATION_DAYS)
    session.add(auth_session)
    session.commit()
    return user


def is_request_authenticated(request: Request) -> bool:
    """Vérification autonome (sa propre session DB) pour le middleware
    global de main.py, qui ne peut pas utiliser `Depends`."""
    from app.database import engine

    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return False
    with DbSession(engine) as session:
        return _touch_session(token, session) is not None


def get_current_user(request: Request, session: DbSession = Depends(get_session)) -> User:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Non authentifié.")
    user = _touch_session(token, session)
    if user is None:
        raise HTTPException(401, "Session invalide ou expirée.")
    return user


@router.get("/status", response_model=AuthStatus)
def auth_status(request: Request, session: DbSession = Depends(get_session)) -> AuthStatus:
    user = session.exec(select(User)).first()
    if user is None:
        return AuthStatus(setup_required=True, authenticated=False)

    token = request.cookies.get(SESSION_COOKIE_NAME)
    authenticated = token is not None and _touch_session(token, session) is not None
    return AuthStatus(setup_required=False, authenticated=authenticated)


@router.post("/setup", response_model=CurrentUser, status_code=201)
def setup(payload: SetupRequest, session: DbSession = Depends(get_session)) -> CurrentUser:
    if session.exec(select(User)).first() is not None:
        raise HTTPException(403, "Un compte administrateur existe déjà.")
    if not payload.username.strip():
        raise HTTPException(400, "Le nom d'utilisateur ne peut pas être vide.")
    if len(payload.password) < 8:
        raise HTTPException(400, "Le mot de passe doit contenir au moins 8 caractères.")

    user = User(id=1, username=payload.username.strip(), password_hash=hash_password(payload.password))
    session.add(user)
    session.commit()
    return CurrentUser(username=user.username)


@router.post("/login", response_model=CurrentUser)
def login(
    payload: LoginRequest, request: Request, response: Response, session: DbSession = Depends(get_session)
) -> CurrentUser:
    user = session.exec(select(User).where(User.username == payload.username)).first()
    generic_error = "Nom d'utilisateur ou mot de passe incorrect."
    if user is None:
        raise HTTPException(401, generic_error)

    now = _utcnow()
    if user.locked_until is not None and user.locked_until > now:
        remaining = int((user.locked_until - now).total_seconds() // 60) + 1
        raise HTTPException(429, f"Compte temporairement verrouillé après trop d'échecs. Réessayez dans {remaining} min.")

    if not verify_password(payload.password, user.password_hash):
        user.failed_attempts += 1
        if user.failed_attempts >= LOCKOUT_THRESHOLD:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_attempts = 0
        session.add(user)
        session.commit()
        raise HTTPException(401, generic_error)

    user.failed_attempts = 0
    user.locked_until = None
    session.add(user)

    token = generate_token()
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=now + timedelta(days=SESSION_DURATION_DAYS),
    )
    session.add(auth_session)
    session.commit()

    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=_cookie_is_secure(request),
        max_age=SESSION_DURATION_DAYS * 24 * 3600,
        path="/",
    )
    return CurrentUser(username=user.username)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, session: DbSession = Depends(get_session)) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        auth_session = session.exec(
            select(AuthSession).where(AuthSession.token_hash == hash_token(token))
        ).first()
        if auth_session is not None:
            session.delete(auth_session)
            session.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.get("/me", response_model=CurrentUser)
def me(user: User = Depends(get_current_user)) -> CurrentUser:
    return CurrentUser(username=user.username)


@router.put("/password", response_model=CurrentUser)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: DbSession = Depends(get_session),
) -> CurrentUser:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(401, "Mot de passe actuel incorrect.")
    if len(payload.new_password) < 8:
        raise HTTPException(400, "Le nouveau mot de passe doit contenir au moins 8 caractères.")

    user.password_hash = hash_password(payload.new_password)
    user.updated_at = _utcnow()
    session.add(user)

    # Le changement de mot de passe invalide toutes les AUTRES sessions —
    # seule celle utilisée pour cette requête reste valide.
    current_token = request.cookies.get(SESSION_COOKIE_NAME)
    current_hash = hash_token(current_token) if current_token else None
    other_sessions = session.exec(
        select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.token_hash != current_hash)
    ).all()
    for s in other_sessions:
        session.delete(s)

    session.commit()
    return CurrentUser(username=user.username)
