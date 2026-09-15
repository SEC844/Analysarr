"""Détection d'une nouvelle version publiée, via l'API GitHub Releases du
dépôt officiel.

- Seule requête sortante : `GET api.github.com/repos/<dépôt>/releases/latest`,
  URL en dur (config.GITHUB_REPOSITORY), aucune donnée de l'utilisateur
  envoyée hormis l'en-tête User-Agent avec la version.
- Résultat mis en cache en mémoire (6 h, 30 min après un échec) : l'API GitHub
  anonyme est limitée à 60 requêtes/heure par IP, et l'interface interroge
  l'état à chaque chargement de page.
- Une vérification forcée ("Vérifier maintenant") reste limitée à une par
  minute pour la même raison.
- Le contenu de la release (notes en Markdown) n'est jamais relayé : seuls le
  numéro de version, la date et un lien validé vers github.com le sont."""

import asyncio
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.config import APP_VERSION, GITHUB_REPOSITORY
from app.schemas.app import UpdateStatus

LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPOSITORY}/releases"

_CACHE_TTL = timedelta(hours=6)
_ERROR_TTL = timedelta(minutes=30)
_FORCE_MIN_INTERVAL = timedelta(minutes=1)

_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

_cached: UpdateStatus | None = None
_expires_at: datetime | None = None
_lock = asyncio.Lock()


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
    global _cached, _expires_at
    async with _lock:
        now = datetime.now(timezone.utc)
        if _cached is not None and _expires_at is not None:
            fresh = _expires_at > now
            too_soon = force and now - _cached.checked_at < _FORCE_MIN_INTERVAL
            if (fresh and not force) or too_soon:
                return _cached
        _cached = await _fetch_latest(now)
        _expires_at = now + (_ERROR_TTL if _cached.error else _CACHE_TTL)
        return _cached


def peek_update_status() -> UpdateStatus | None:
    """Dernier résultat connu, sans jamais déclencher de requête (vérification
    automatique désactivée)."""
    return _cached
