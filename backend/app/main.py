from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from app.database import engine, init_db
from app.models.settings import Settings
from app.routers import auth as auth_router
from app.routers import media as media_router
from app.routers import scan as scan_router
from app.routers import settings as settings_router
from app.routers.auth import is_request_authenticated
from app.services.scheduler import configure_scan_schedule, scheduler

# Chemins sous /api/ accessibles sans session : l'auth elle-même (login/setup/
# statut/déconnexion) et le healthcheck Docker.
_PUBLIC_API_PREFIXES = ("/api/auth/",)
_PUBLIC_API_PATHS = ("/api/health",)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with Session(engine) as session:
        settings = session.get(Settings, 1)
        if settings is not None and settings.scan_schedule_enabled:
            configure_scan_schedule(settings.scan_schedule_interval_minutes)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Analysarr", lifespan=lifespan)


@app.middleware("http")
async def require_auth(request: Request, call_next):
    path = request.url.path
    is_public = path in _PUBLIC_API_PATHS or any(path.startswith(p) for p in _PUBLIC_API_PREFIXES)
    if path.startswith("/api/") and not is_public:
        if not is_request_authenticated(request):
            return JSONResponse({"detail": "Non authentifié."}, status_code=401)
    return await call_next(request)


@app.get("/api/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}


app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(scan_router.router, prefix="/api/scan", tags=["scan"])
app.include_router(media_router.router, prefix="/api/media", tags=["media"])

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
