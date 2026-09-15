from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.database import get_session
from app.models.settings import Settings
from app.schemas.services import ServicesStatus
from app.services.service_status import services_status

router = APIRouter()


@router.get("/status", response_model=ServicesStatus)
async def get_services_status(refresh: bool = False, session: Session = Depends(get_session)) -> ServicesStatus:
    """Statut de connexion de chaque service configuré. `refresh` ignore le
    cache (au plus une vérification toutes les 10 s)."""
    return await services_status(session, session.get(Settings, 1), refresh)
