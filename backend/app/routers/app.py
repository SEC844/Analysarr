from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlmodel import Session

from app.config import APP_BUILD_DATE, APP_REVISION, APP_VERSION, GITHUB_REPOSITORY
from app.database import get_session
from app.models.settings import Settings
from app.schemas.app import AppInfo, AppPreferencesWrite, UiPreferences, UpdateStatus
from app.services.scheduler import refresh_update_watch
from app.services.updates import get_update_status, status_for_page

router = APIRouter()


def ui_preferences(settings: Settings | None) -> UiPreferences:
    """Préférences enregistrées ; valeurs par défaut si absentes ou illisibles."""
    try:
        return UiPreferences.model_validate_json(settings.ui_preferences) if settings else UiPreferences()
    except ValidationError:
        return UiPreferences()


def _to_info(settings: Settings | None, update: UpdateStatus | None) -> AppInfo:
    return AppInfo(
        version=APP_VERSION,
        revision=APP_REVISION,
        build_date=APP_BUILD_DATE,
        repository_url=f"https://github.com/{GITHUB_REPOSITORY}",
        language=settings.language if settings and settings.language in ("fr", "en") else None,
        update_check_enabled=settings.update_check_enabled if settings else True,
        update=update,
        ui=ui_preferences(settings),
    )


@router.get("/info", response_model=AppInfo)
async def get_app_info(session: Session = Depends(get_session)) -> AppInfo:
    settings = session.get(Settings, 1)
    enabled = settings.update_check_enabled if settings else True
    return _to_info(settings, await status_for_page(enabled))


@router.post("/check-updates", response_model=AppInfo)
async def check_updates(session: Session = Depends(get_session)) -> AppInfo:
    """Vérification explicite demandée par l'utilisateur : autorisée même si
    la vérification automatique est désactivée."""
    return _to_info(session.get(Settings, 1), await get_update_status(force=True))


@router.put("/preferences", response_model=AppInfo)
async def put_preferences(payload: AppPreferencesWrite, session: Session = Depends(get_session)) -> AppInfo:
    row = session.get(Settings, 1)
    if row is None:
        row = Settings(id=1)
    row.language = payload.language
    row.update_check_enabled = payload.update_check_enabled
    if payload.ui is not None:
        row.ui_preferences = payload.ui.model_dump_json()
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    # La vérification périodique ne sert qu'aux notifications : elle suit
    # l'interrupteur de vérification des mises à jour.
    refresh_update_watch(session)
    return _to_info(row, await status_for_page(row.update_check_enabled))
