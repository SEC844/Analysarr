import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlmodel import Session as DbSession
from sqlmodel import select

from app.database import get_session
from app.models.auth import Session as AuthSession
from app.models.auth import User
from app.schemas.auth import (
    AuthStatus,
    ChangePasswordRequest,
    ChangeUsernameRequest,
    CurrentUser,
    LoginRequest,
    PasswordConfirmation,
    RecoveryCodes,
    SetupRequest,
    TwoFactorCode,
    TwoFactorDisableRequest,
    TwoFactorSetup,
)
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
from app.services.totp import (
    generate_recovery_codes,
    generate_secret,
    hash_recovery_code,
    provisioning_uri,
    verify_code,
)

router = APIRouter()

MAX_USERNAME_LENGTH = 64


def _utcnow() -> datetime:
    # Naïf volontairement : cohérent avec app.models.auth._utcnow (voir son
    # commentaire) — SQLite relit toujours les datetimes sans tzinfo.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cookie_is_secure(request: Request) -> bool:
    """Le cookie `Secure` n'est posé que si la requête est vue en HTTPS —
    jamais en dur, sinon la connexion casse pour un accès direct en HTTP
    (ex: http://10.0.20.110:1818, sans reverse-proxy TLS devant)."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


def _current_user(user: User) -> CurrentUser:
    return CurrentUser(username=user.username, two_factor_enabled=bool(user.totp_secret))


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


def _revoke_other_sessions(request: Request, user: User, session: DbSession) -> None:
    """Supprime toutes les sessions de l'utilisateur SAUF celle utilisée pour
    cette requête (l'appelant commit)."""
    current_token = request.cookies.get(SESSION_COOKIE_NAME)
    current_hash = hash_token(current_token) if current_token else None
    other_sessions = session.exec(
        select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.token_hash != current_hash)
    ).all()
    for s in other_sessions:
        session.delete(s)


def _check_second_factor(user: User, code: str) -> bool:
    """Code TOTP (jamais rejouable) ou code de secours (consommé). Modifie
    `user` sans commit."""
    if not user.totp_secret:
        return False
    step = verify_code(user.totp_secret, code, user.totp_last_step)
    if step is not None:
        user.totp_last_step = step
        return True
    hashes = json.loads(user.recovery_codes or "[]")
    code_hash = hash_recovery_code(code)
    if code_hash in hashes:
        hashes.remove(code_hash)
        user.recovery_codes = json.dumps(hashes)
        return True
    return False


def _register_failure(user: User, now: datetime, session: DbSession) -> None:
    user.failed_attempts += 1
    if user.failed_attempts >= LOCKOUT_THRESHOLD:
        user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
        user.failed_attempts = 0
    session.add(user)
    session.commit()


def _two_factor_required(message: str) -> JSONResponse:
    return JSONResponse({"detail": message, "two_factor_required": True}, status_code=401)


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
    return _current_user(user)


@router.post("/login", response_model=CurrentUser)
def login(
    payload: LoginRequest, request: Request, response: Response, session: DbSession = Depends(get_session)
) -> CurrentUser | JSONResponse:
    user = session.exec(select(User).where(User.username == payload.username)).first()
    generic_error = "Nom d'utilisateur ou mot de passe incorrect."
    if user is None:
        raise HTTPException(401, generic_error)

    now = _utcnow()
    if user.locked_until is not None and user.locked_until > now:
        remaining = int((user.locked_until - now).total_seconds() // 60) + 1
        raise HTTPException(429, f"Compte temporairement verrouillé après trop d'échecs. Réessayez dans {remaining} min.")

    if not verify_password(payload.password, user.password_hash):
        _register_failure(user, now, session)
        raise HTTPException(401, generic_error)

    if user.totp_secret:
        if not payload.otp:
            # Mot de passe correct : le second facteur est demandé, sans
            # compter d'échec. Un mauvais code, lui, compte pour le verrouillage.
            return _two_factor_required("Code de double authentification requis.")
        if not _check_second_factor(user, payload.otp):
            _register_failure(user, now, session)
            return _two_factor_required("Code de double authentification incorrect.")

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
    return _current_user(user)


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
    return _current_user(user)


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
    _revoke_other_sessions(request, user, session)

    session.commit()
    return _current_user(user)


@router.put("/username", response_model=CurrentUser)
def change_username(
    payload: ChangeUsernameRequest,
    user: User = Depends(get_current_user),
    session: DbSession = Depends(get_session),
) -> CurrentUser:
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Mot de passe incorrect.")
    username = payload.username.strip()
    if not username:
        raise HTTPException(400, "Le nom d'utilisateur ne peut pas être vide.")
    if len(username) > MAX_USERNAME_LENGTH:
        raise HTTPException(400, f"Le nom d'utilisateur ne peut pas dépasser {MAX_USERNAME_LENGTH} caractères.")

    user.username = username
    user.updated_at = _utcnow()
    session.add(user)
    session.commit()
    return _current_user(user)


@router.post("/2fa/setup", response_model=TwoFactorSetup)
def two_factor_setup(
    payload: PasswordConfirmation,
    user: User = Depends(get_current_user),
    session: DbSession = Depends(get_session),
) -> TwoFactorSetup:
    """Génère un secret en attente : la 2FA n'est active qu'après un premier
    code valide (`/2fa/enable`), jamais sur la seule génération."""
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Mot de passe incorrect.")
    if user.totp_secret:
        raise HTTPException(409, "La double authentification est déjà activée.")

    user.totp_pending_secret = generate_secret()
    session.add(user)
    session.commit()
    return TwoFactorSetup(
        secret=user.totp_pending_secret, otpauth_uri=provisioning_uri(user.totp_pending_secret, user.username)
    )


@router.post("/2fa/enable", response_model=RecoveryCodes)
def two_factor_enable(
    payload: TwoFactorCode,
    request: Request,
    user: User = Depends(get_current_user),
    session: DbSession = Depends(get_session),
) -> RecoveryCodes:
    if user.totp_secret:
        raise HTTPException(409, "La double authentification est déjà activée.")
    if not user.totp_pending_secret:
        raise HTTPException(400, "Aucune configuration en cours : recommencez la configuration.")
    step = verify_code(user.totp_pending_secret, payload.code, None)
    if step is None:
        raise HTTPException(400, "Code incorrect : vérifiez l'heure de votre téléphone et réessayez.")

    codes = generate_recovery_codes()
    user.totp_secret = user.totp_pending_secret
    user.totp_pending_secret = None
    user.totp_last_step = step
    user.recovery_codes = json.dumps([hash_recovery_code(c) for c in codes])
    user.updated_at = _utcnow()
    session.add(user)
    # Les sessions ouvertes sans second facteur ne restent pas valides.
    _revoke_other_sessions(request, user, session)
    session.commit()
    return RecoveryCodes(codes=codes)


@router.post("/2fa/disable", response_model=CurrentUser)
def two_factor_disable(
    payload: TwoFactorDisableRequest,
    user: User = Depends(get_current_user),
    session: DbSession = Depends(get_session),
) -> CurrentUser:
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Mot de passe incorrect.")
    if not user.totp_secret:
        raise HTTPException(400, "La double authentification n'est pas activée.")
    if not _check_second_factor(user, payload.code):
        raise HTTPException(401, "Code de double authentification incorrect.")

    user.totp_secret = None
    user.totp_pending_secret = None
    user.totp_last_step = None
    user.recovery_codes = "[]"
    user.updated_at = _utcnow()
    session.add(user)
    session.commit()
    return _current_user(user)
