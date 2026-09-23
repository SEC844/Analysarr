"""Détection d'une nouvelle version publiée, via l'API GitHub Releases du
dépôt officiel.

- Seule requête sortante : `GET api.github.com/repos/<dépôt>/releases/latest`,
  URL en dur (config.GITHUB_REPOSITORY), aucune donnée de l'utilisateur
  envoyée hormis l'en-tête User-Agent avec la version.
- Vérification déclenchée par l'ouverture de la page, jamais par une minuterie
  (sauf notification, voir plus bas) : le cache mémoire ne vaut que 10 minutes
  (30 min après un échec) et son rafraîchissement ne bloque jamais la réponse.
  Une seule requête sortante par minute au maximum, y compris pour une
  vérification forcée ("Vérifier maintenant") : l'API GitHub anonyme est
  limitée à 60 requêtes/heure par IP.
- `notify_update_available` : vérification périodique (toutes les 3 h, voir
  `services/scheduler.py`) planifiée UNIQUEMENT si un canal de notification est
  abonné à l'événement `update_available`. Une seule notification par version
  publiée (`Settings.update_notified_version`).
- Le contenu de la release (notes en Markdown) n'est jamais relayé : seuls le
  numéro de version, la date et un lien validé vers github.com le sont."""

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
from sqlmodel import Session

from app.config import APP_VERSION, GITHUB_REPOSITORY
from app.database import engine
from app.models.settings import Settings
from app.schemas.app import UpdateStatus
from app.services.notifications import channel_targets, notify, update_available_notification

logger = logging.getLogger("analysarr.updates")

LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPOSITORY}/releases"

_CACHE_TTL = timedelta(minutes=10)
_ERROR_TTL = timedelta(minutes=30)
_FORCE_MIN_INTERVAL = timedelta(minutes=1)

_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")



class _UpdateCache:
    """Dernier état connu, son expiration et le rafraîchissement en cours.
    Un seul exemplaire, au niveau du module."""

    def __init__(self) -> None:
        self.status: UpdateStatus | None = None
        self.expires_at: datetime | None = None
        self.lock = asyncio.Lock()
        # Référence forte vers le rafraîchissement en cours : sans elle, le
        # ramasse-miettes peut annuler la tâche avant sa fin.
        self.refresh_task: asyncio.Task | None = None

    def is_stale(self, now: datetime) -> bool:
        return self.expires_at is None or self.expires_at <= now

    def clear(self) -> None:
        self.status = None
        self.expires_at = None
        self.refresh_task = None


_cache = _UpdateCache()


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    match = _VERSION_PATTERN.match(value or "")
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


async def _fetch_latest(now: datetime) -> UpdateStatus:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                LATEST_RELEASE_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": f"Analysarr/{APP_VERSION}",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return UpdateStatus(checked_at=now, error=str(exc) or type(exc).__name__)

    latest = parse_version(str(data.get("tag_name") or ""))
    if latest is None:
        return UpdateStatus(checked_at=now, error="Version publiée illisible.")

    current = parse_version(APP_VERSION)
    release_url = data.get("html_url")
    if not (isinstance(release_url, str) and release_url.startswith(f"{RELEASES_PAGE}/")):
        release_url = RELEASES_PAGE  # jamais un lien arbitraire affiché dans l'interface

    return UpdateStatus(
        checked_at=now,
        latest_version=".".join(map(str, latest)),
        release_url=release_url,
        published_at=data.get("published_at"),
        update_available=current is not None and latest > current,
    )


async def get_update_status(force: bool = False) -> UpdateStatus:
    async with _cache.lock:
        now = datetime.now(UTC)
        cached, expires_at = _cache.status, _cache.expires_at
        if cached is not None and expires_at is not None:
            fresh = expires_at > now
            too_soon = force and now - cached.checked_at < _FORCE_MIN_INTERVAL
            if (fresh and not force) or too_soon:
                return cached
        status = await _fetch_latest(now)
        _cache.status = status
        _cache.expires_at = now + (_ERROR_TTL if status.error else _CACHE_TTL)
        return status


def refresh_in_background() -> None:
    """Rafraîchit le cache sans faire attendre l'appelant. Sans effet si le
    cache est encore frais ou si un rafraîchissement est déjà en cours."""
    if not _cache.is_stale(datetime.now(UTC)):
        return
    if _cache.refresh_task is not None and not _cache.refresh_task.done():
        return
    async def refresh() -> None:
        try:
            await get_update_status()
        except Exception as exc:  # jamais d'exception perdue dans une tâche de fond
            logger.warning("Vérification des mises à jour impossible : %s", type(exc).__name__)

    try:
        _cache.refresh_task = asyncio.get_running_loop().create_task(refresh())
    except RuntimeError:
        # Hors boucle asyncio : ne devrait pas arriver dans l'application.
        logger.warning("Vérification des mises à jour non lancée : aucune boucle asyncio")
        _cache.refresh_task = None


async def status_for_page(enabled: bool) -> UpdateStatus | None:
    """État affiché au chargement d'une page. Premier appel : on attend le
    résultat, sinon l'interface n'aurait rien à montrer. Ensuite, on renvoie
    toujours le dernier résultat connu et on rafraîchit en tâche de fond."""
    if not enabled:
        return _cache.status
    if _cache.status is None:
        return await get_update_status()
    refresh_in_background()
    return _cache.status


async def notify_update_available() -> None:
    """Vérification périodique réservée aux notifications : une seule
    notification par version publiée, et rien du tout si la vérification
    automatique est désactivée."""

    with Session(engine) as session:
        settings = session.get(Settings, 1) or Settings(id=1)
        if not settings.update_check_enabled:
            return
        targets = [target for target in channel_targets(session) if target.wants("update_available")]
        if not targets:
            return
        status = await get_update_status()
        if not (status.update_available and status.latest_version):
            return
        if settings.update_notified_version == status.latest_version:
            return
        language = settings.language if settings.language in ("fr", "en") else "fr"
        # Marqué avant l'envoi : l'envoi est en tâche de fond, et mieux vaut une
        # notification perdue qu'une notification toutes les 3 h.
        settings.update_notified_version = status.latest_version
        session.add(settings)
        session.commit()

    notify(
        targets,
        "update_available",
        update_available_notification(language, APP_VERSION, status.latest_version, status.release_url),
    )
