"""Statut de connexion de chaque service configuré, affiché en permanence dans
l'en-tête de l'interface.

- Mêmes vérifications que « Tester la connexion » (connection_test.TESTERS),
  avec les réglages ENREGISTRÉS — jamais une URL fournie par la requête.
- Cache de 60 s partagé par tous les onglets : l'interface peut interroger
  régulièrement sans multiplier les requêtes vers les services. Une
  vérification forcée n'est refaite qu'au bout de 10 s, et tout changement de
  réglage (URL, clé, instance) invalide le cache.
- Aucun secret n'est renvoyé : seuls le nom du service, le résultat et le
  message du test (tronqué)."""

import asyncio
import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session

from app.clients.emby import media_server_name
from app.clients.torrent import torrent_client_configured, torrent_client_kind, torrent_client_name
from app.models.settings import Settings
from app.schemas.services import ServicesStatus, ServiceStatusRead
from app.schemas.settings import ConnectionTestRequest, ConnectionTestResult, MediaServer
from app.services.arr_instances import arr_targets
from app.services.connection_test import TESTERS
from app.services.seer import seer_configured

CACHE_SECONDS = 60
MIN_REFRESH_SECONDS = 10
# Au-delà, le statut n'est plus exposé au widget externe (voir cached_services_status).
STALE_SECONDS = 600
_MAX_MESSAGE_LENGTH = 300

_clock = time.monotonic


@dataclass(frozen=True)
class _Check:
    service: str
    name: str
    request: ConnectionTestRequest


@dataclass(frozen=True)
class _Cached:
    at: float
    signature: str
    status: ServicesStatus


class _StatusCache:
    """Dernier statut calculé. Un seul exemplaire, au niveau du module."""

    def __init__(self) -> None:
        self.entry: _Cached | None = None

    def clear(self) -> None:
        self.entry = None


_cache = _StatusCache()


def _checks(session: Session, settings: Settings | None) -> list[_Check]:
    if settings is None:
        return []
    checks: list[_Check] = []
    if settings.emby_url and settings.emby_api_key:
        server: MediaServer = "jellyfin" if settings.media_server == "jellyfin" else "emby"
        request = ConnectionTestRequest(url=settings.emby_url, api_key=settings.emby_api_key, media_server=server)
        checks.append(_Check("emby", media_server_name(settings), request))
    for kind in ("sonarr", "radarr"):
        for target in arr_targets(session, settings, kind):
            checks.append(_Check(kind, target.name, ConnectionTestRequest(url=target.url, api_key=target.api_key)))
    if torrent_client_configured(settings):
        request = ConnectionTestRequest(
            url=settings.qbittorrent_url,
            username=settings.qbittorrent_username,
            password=settings.qbittorrent_password,
            torrent_client=torrent_client_kind(settings),
        )
        checks.append(_Check("qbittorrent", torrent_client_name(settings), request))
    if settings.cross_seed_enabled and settings.cross_seed_url:
        checks.append(_Check("cross_seed", "cross-seed", ConnectionTestRequest(url=settings.cross_seed_url)))
    if seer_configured(settings):
        checks.append(
            _Check("seer", "Seer", ConnectionTestRequest(url=settings.seer_url, api_key=settings.seer_api_key))
        )
    return checks


def _signature(checks: list[_Check]) -> str:
    # Empreinte seulement : les secrets n'y sont jamais conservés en clair.
    raw = repr([(check.service, check.name, check.request.model_dump()) for check in checks])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _run(check: _Check) -> ServiceStatusRead:
    try:
        result = await TESTERS[check.service](check.request)
    except Exception as exc:  # noqa: BLE001 - un service défaillant ne doit jamais faire échouer le statut global
        result = ConnectionTestResult(success=False, message=f"Vérification impossible : {type(exc).__name__}")
    return ServiceStatusRead(
        service=check.service, name=check.name, ok=result.success, message=result.message[:_MAX_MESSAGE_LENGTH]
    )


async def services_status(session: Session, settings: Settings | None, refresh: bool = False) -> ServicesStatus:
    checks = _checks(session, settings)
    signature = _signature(checks)
    cached = _cache.entry
    if (
        cached is not None
        and cached.signature == signature
        and _clock() - cached.at < (MIN_REFRESH_SECONDS if refresh else CACHE_SECONDS)
    ):
        return cached.status
    results = await asyncio.gather(*(_run(check) for check in checks))
    status = ServicesStatus(checked_at=datetime.now(UTC), services=list(results))
    _cache.entry = _Cached(at=_clock(), signature=signature, status=status)
    return status


def cached_services_status() -> ServicesStatus | None:
    """Dernier statut connu, sans aucune requête sortante : un appel public
    (widget) ne doit jamais pouvoir déclencher des connexions vers les services."""
    cached = _cache.entry
    if cached is None or _clock() - cached.at > STALE_SECONDS:
        return None
    return cached.status
