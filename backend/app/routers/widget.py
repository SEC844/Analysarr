"""Widget de tableau de bord externe (Homepage, Homarr...) : `GET /api/status`.

Accessible sans session (voir main.py), protégé par une clé API dédiée
générée dans Réglages → Widget :
- seul le hash sha256 de la clé est stocké, comparaison à temps constant ;
- clé acceptée dans l'en-tête `X-Api-Key` ou `Authorization: Bearer`, jamais
  en paramètre d'URL (elle finirait dans les journaux des proxys) ;
- réponse limitée à des compteurs, et aucune requête sortante déclenchée : le
  statut des services est celui déjà en cache."""

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import Session, col, select

from app.config import APP_VERSION
from app.database import get_session
from app.models.media import Media, MediaType, ScanRun
from app.models.settings import Settings
from app.schemas.widget import WidgetLastScan, WidgetMediaCounts, WidgetServices, WidgetStatus
from app.services.security import hash_token
from app.services.service_status import cached_services_status
from app.services.watch_stats import as_utc

router = APIRouter()

STATUS_KEYS = ("doublon", "orphelin_qbit", "non_hardlink", "tracker_unique", "manquant_emby", "manquant_qbit")


def _presented_key(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    return credentials.strip() or None if scheme.lower() == "bearer" else None


@router.get("", response_model=WidgetStatus)
def widget_status(request: Request, response: Response, session: Session = Depends(get_session)) -> WidgetStatus:
    settings = session.get(Settings, 1)
    expected = settings.widget_api_key_hash if settings is not None else None
    key = _presented_key(request)
    # Même réponse que le widget soit désactivé ou la clé fausse.
    if not expected or not key or not hmac.compare_digest(hash_token(key), expected):
        raise HTTPException(401, "Clé API invalide.")
    response.headers["Cache-Control"] = "no-store"

    medias = session.exec(select(Media)).all()
    statuses = dict.fromkeys(STATUS_KEYS, 0)
    for media in medias:
        for status in media.statuses.split(","):
            if status in statuses:
                statuses[status] += 1

    last_run = session.exec(select(ScanRun).order_by(col(ScanRun.id).desc())).first()
    services = cached_services_status()

    return WidgetStatus(
        version=APP_VERSION,
        media=WidgetMediaCounts(
            total=len(medias),
            movies=sum(1 for m in medias if m.media_type == MediaType.movie),
            series=sum(1 for m in medias if m.media_type == MediaType.series),
            healthy=sum(1 for m in medias if not m.statuses),
        ),
        statuses=statuses,
        reclaimable_bytes=sum(m.reclaimable_bytes for m in medias),
        total_size_bytes=sum(m.total_size for m in medias),
        last_scan=WidgetLastScan(
            status=last_run.status.value,
            started_at=as_utc(last_run.started_at),
            finished_at=as_utc(last_run.finished_at),
        )
        if last_run is not None
        else None,
        services=WidgetServices(ok=sum(1 for s in services.services if s.ok), total=len(services.services))
        if services is not None
        else None,
    )
