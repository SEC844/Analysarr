from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class WidgetMediaCounts(BaseModel):
    total: int
    movies: int
    series: int
    healthy: int


class WidgetLastScan(BaseModel):
    status: str  # running | completed | failed
    started_at: datetime
    finished_at: Optional[datetime]


class WidgetServices(BaseModel):
    ok: int
    total: int


class WidgetStatus(BaseModel):
    """Résumé public du widget : uniquement des compteurs, jamais de titre,
    de chemin, de nom d'utilisateur ni d'adresse de service."""

    version: str
    media: WidgetMediaCounts
    # Nombre de médias par statut (doublon, orphelin_qbit...).
    statuses: dict[str, int]
    reclaimable_bytes: int
    total_size_bytes: int
    last_scan: Optional[WidgetLastScan]
    # Dernier statut connu des services ; None si aucune vérification récente.
    services: Optional[WidgetServices]
