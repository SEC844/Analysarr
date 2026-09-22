from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from app.database import engine, init_db
from app.models.settings import Settings
from app.routers import app as app_router
from app.routers import auth as auth_router
from app.routers import automations as automations_router
from app.routers import emby as emby_router
from app.routers import history as history_router
from app.routers import media as media_router
from app.routers import notifications as notifications_router
from app.routers import scan as scan_router
from app.routers import services as services_router
from app.routers import settings as settings_router
from app.routers import trash as trash_router
from app.routers import widget as widget_router
from app.routers.auth import is_request_authenticated
from app.services.login_log import record_attempt
from app.services.path_guard import MountUnavailableError
from app.services.rate_limit import retry_after
from app.services.security import client_ip, parse_trusted_proxies
from app.services.scheduler import configure_scan_schedule, configure_trash_purge, refresh_update_watch, scheduler

# Chemins sous /api/ accessibles sans session : l'auth elle-même (login/setup/
# statut/déconnexion) et le healthcheck Docker.
_PUBLIC_API_PREFIXES = ("/api/auth/",)
# `/api/status` : widget externe, protégé par sa propre clé API (routers/widget.py).
_PUBLIC_API_PATHS = ("/api/health", "/api/status")
# Exception dans `/api/auth/` : le journal de connexion n'a rien de public.
_PROTECTED_AUTH_PATHS = ("/api/auth/login-history", "/api/auth/security")
# Routes d'authentification limitées en débit (par adresse IP) : le
# verrouillage de compte ne couvre ni le coût d'un bcrypt par requête, ni les
# routes 2FA. La lecture d'état (`/status`, `/me`) n'est jamais limitée : le
# frontend l'appelle à chaque chargement de page.
_RATE_LIMITED_AUTH_PATHS = (
    "/api/auth/login",
    "/api/auth/setup",
    "/api/auth/password",
    "/api/auth/username",
    "/api/auth/2fa/setup",
    "/api/auth/2fa/enable",
    "/api/auth/2fa/disable",
)
# En-têtes de sécurité appliqués à TOUTE réponse. CSP volontairement stricte :
# l'application ne charge aucun script ni aucune image hors de sa propre
# origine (jaquettes et avatars sont relayés par l'API). `unsafe-inline` reste
# nécessaire pour les styles, posés en attribut par React et Base UI.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)
_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with Session(engine) as session:
        settings = session.get(Settings, 1)
        if settings is not None and settings.scan_schedule_enabled:
            configure_scan_schedule(settings.scan_schedule_interval_minutes)
        refresh_update_watch(session)
    configure_trash_purge()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Analysarr", lifespan=lifespan)


@app.exception_handler(MountUnavailableError)
async def mount_unavailable(request: Request, exc: MountUnavailableError) -> JSONResponse:
    """Action refusée faute de montage : 409 plutôt que 500, avec le message
    exact à afficher (voir services/path_guard.py)."""
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.middleware("http")
async def limit_auth_requests(request: Request, call_next):
    """Fenêtre glissante par adresse IP sur les routes d'authentification
    (voir services/rate_limit.py). L'adresse n'est lue dans X-Forwarded-For
    que derrière un proxy déclaré de confiance."""
    if request.url.path not in _RATE_LIMITED_AUTH_PATHS:
        return await call_next(request)

    with Session(engine) as session:
        settings = session.get(Settings, 1)
        trusted = parse_trusted_proxies(settings.trusted_proxies if settings else "")
        ip = client_ip(request, trusted)
        wait = retry_after(ip or "inconnu")
        if wait is not None:
            if request.url.path == "/api/auth/login":
                record_attempt(session, username="", ip=ip, success=False, reason="rate_limited")
            return JSONResponse(
                {"detail": f"Trop de tentatives. Réessayez dans {wait} s."},
                status_code=429,
                headers={"Retry-After": str(wait)},
            )
    return await call_next(request)


@app.middleware("http")
async def require_auth(request: Request, call_next):
    path = request.url.path
    is_public = (
        path in _PUBLIC_API_PATHS
        or any(path.startswith(p) for p in _PUBLIC_API_PREFIXES)
    ) and path not in _PROTECTED_AUTH_PATHS
    if path.startswith("/api/") and not is_public:
        if not is_request_authenticated(request):
            return JSONResponse({"detail": "Non authentifié."}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def static_cache_headers(request: Request, call_next):
    """Plus besoin de Ctrl+F5 après une mise à jour : la page (index.html) est
    toujours revalidée auprès du serveur, et les fichiers de /assets — dont le
    nom contient une empreinte qui change à chaque build — peuvent rester en
    cache indéfiniment sans jamais servir une ancienne version."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        # Aucune réponse d'API n'est mise en cache : sans directive explicite,
        # un cache intermédiaire (reverse-proxy, CDN) peut appliquer sa propre
        # fraîcheur heuristique et resservir un état d'authentification périmé
        # — l'interface boucle alors sur des 401. Les routes qui posent leur
        # propre en-tête (jaquettes, avatars, flux SSE) gardent le leur.
        response.headers.setdefault("Cache-Control", "no-store")
        return response
    if path.startswith("/assets/") and response.status_code == 200:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Durcissement pour une instance exposée : ces en-têtes ne changent rien
    en réseau local et coupent le clickjacking, le sniffing de type et le
    chargement de code tiers."""
    response = await call_next(request)
    for header, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


@app.get("/api/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}


app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
app.include_router(app_router.router, prefix="/api/app", tags=["app"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(scan_router.router, prefix="/api/scan", tags=["scan"])
app.include_router(media_router.router, prefix="/api/media", tags=["media"])
app.include_router(emby_router.router, prefix="/api/emby", tags=["emby"])
app.include_router(history_router.router, prefix="/api/history", tags=["history"])
app.include_router(notifications_router.router, prefix="/api/notifications", tags=["notifications"])
app.include_router(automations_router.router, prefix="/api/automations", tags=["automations"])
app.include_router(services_router.router, prefix="/api/services", tags=["services"])
app.include_router(trash_router.router, prefix="/api/trash", tags=["trash"])
app.include_router(widget_router.router, prefix="/api/status", tags=["widget"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

if STATIC_DIR.exists():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
