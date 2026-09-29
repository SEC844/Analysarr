"""Assistant de nettoyage (voir services/cleanup.py) : lecture seule. La
suppression passe par la route existante de suppression sélective. Routes
synchrones : FastAPI les exécute hors de la boucle asyncio."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.database import get_session
from app.models.settings import Settings
from app.schemas.cleanup import (
    CleanupCandidateDetail,
    CleanupCandidatesPage,
    CleanupSettings,
    CleanupSettingsRead,
)
from app.services.cleanup import CandidateQuery, candidate_detail, candidates_page, cleanup_settings
from app.services.cleanup_score import PRESETS

router = APIRouter()


@router.get("/candidates", response_model=CleanupCandidatesPage)
def list_candidates(
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=100),
    sort: Literal["rank", "score", "space", "title"] = "rank",
    media_type: Literal["movie", "series"] | None = None,
    min_score: int = Query(0, ge=0, le=100),
    search: str = Query("", max_length=100),
    include_protected: bool = False,
    session: Session = Depends(get_session),
) -> CleanupCandidatesPage:
    query = CandidateQuery(
        page=page,
        page_size=page_size,
        sort=sort,
        media_type=media_type,
        min_score=min_score,
        search=search,
        include_protected=include_protected,
    )
    return candidates_page(session, query)


@router.get("/candidates/{media_id}", response_model=CleanupCandidateDetail)
def read_candidate(media_id: int, session: Session = Depends(get_session)) -> CleanupCandidateDetail:
    detail = candidate_detail(session, media_id)
    if detail is None:
        raise HTTPException(404, "Média introuvable ou sans rien à libérer.")
    return detail


def _settings_read(session: Session) -> CleanupSettingsRead:
    return CleanupSettingsRead(
        settings=cleanup_settings(session.get(Settings, 1)),
        presets={name: preset for name, preset in PRESETS.items()},
    )


@router.get("/settings", response_model=CleanupSettingsRead)
def read_settings(session: Session = Depends(get_session)) -> CleanupSettingsRead:
    return _settings_read(session)


@router.put("/settings", response_model=CleanupSettingsRead)
def update_settings(payload: CleanupSettings, session: Session = Depends(get_session)) -> CleanupSettingsRead:
    settings = session.get(Settings, 1) or Settings(id=1)
    settings.cleanup_settings = payload.model_dump_json()
    session.add(settings)
    session.commit()
    return _settings_read(session)
