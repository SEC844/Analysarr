"""Notifications vers Discord, ntfy et Gotify — toutes optionnelles.

- Au mieux : un canal injoignable ne fait jamais échouer un scan ni une
  suppression (envoi en tâche de fond, erreurs journalisées).
- Secrets : l'URL d'un webhook Discord ou un jeton ntfy/Gotify donne le droit
  d'écrire dans le canal — jamais journalisés ni renvoyés tels quels (les
  messages d'erreur httpx contiennent l'URL complète : seuls le code HTTP ou
  le type d'erreur sont conservés).
- Discord : seules les URL officielles de webhook sont acceptées, pour ne pas
  transformer Analysarr en relais HTTP vers n'importe quelle adresse."""

import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.models.settings import Settings

logger = logging.getLogger("analysarr.notifications")

_TIMEOUT = 10.0
# Références fortes vers les envois en cours (sinon le ramasse-miettes peut
# annuler une tâche asyncio avant sa fin).
_pending: set[asyncio.Task] = set()

DISCORD_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
    "https://ptb.discord.com/api/webhooks/",
    "https://canary.discord.com/api/webhooks/",
)

# Événement -> préférence qui l'active (None : toujours envoyé).
_EVENT_SETTING = {
    "scan_completed": "notify_on_scan",
    "scan_failed": "notify_on_scan_failure",
    "delete_selection": "notify_on_actions",
    "cascade_delete": "notify_on_actions",
    "hardlink_repair": "notify_on_actions",
    "test": None,
}

_MESSAGES = {
    "fr": {
        "scan_completed": ("Scan terminé", "{media} médias · {duplicates} doublons · {orphans} orphelins"),
        "scan_failed": ("Échec du scan", "{error}"),
        "delete_selection": ("Suppression effectuée", "{title} : {success} élément(s) supprimé(s){failures}"),
        "cascade_delete": ("Nettoyage effectué", "{title} : {success} élément(s) supprimé(s){failures}"),
        "hardlink_repair": ("Hardlinks réparés", "{title} : {success} fichier(s) réparé(s){failures}"),
        "test": ("Notification de test", "Les notifications d'Analysarr fonctionnent."),
        "failures": " · {count} échec(s)",
    },
    "en": {
        "scan_completed": ("Scan completed", "{media} media · {duplicates} duplicates · {orphans} orphans"),
        "scan_failed": ("Scan failed", "{error}"),
        "delete_selection": ("Deletion completed", "{title}: {success} item(s) deleted{failures}"),
        "cascade_delete": ("Cleanup completed", "{title}: {success} item(s) deleted{failures}"),
        "hardlink_repair": ("Hardlinks repaired", "{title}: {success} file(s) repaired{failures}"),
        "test": ("Test notification", "Analysarr notifications are working."),
        "failures": " · {count} failed",
    },
}


@dataclass(frozen=True)
class Targets:
    """Copie des réglages de notification au moment de l'événement : l'envoi
    se fait en tâche de fond, après la fermeture de la session de base."""

    discord_webhook: str | None
    ntfy_url: str | None
    ntfy_token: str | None
    gotify_url: str | None
    gotify_token: str | None

    @property
    def channels(self) -> list[str]:
        return [
            name
            for name, enabled in (
                ("discord", bool(self.discord_webhook)),
                ("ntfy", bool(self.ntfy_url)),
                ("gotify", bool(self.gotify_url and self.gotify_token)),
            )
            if enabled
        ]


def targets_from(settings: Settings | None) -> Targets:
    return Targets(
        discord_webhook=settings.notify_discord_webhook if settings else None,
        ntfy_url=settings.notify_ntfy_url if settings else None,
        ntfy_token=settings.notify_ntfy_token if settings else None,
        gotify_url=settings.notify_gotify_url if settings else None,
        gotify_token=settings.notify_gotify_token if settings else None,
    )


def is_discord_webhook(url: str) -> bool:
    return url.startswith(DISCORD_WEBHOOK_PREFIXES)


def is_http_url(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def render(language: str | None, event: str, **values: object) -> tuple[str, str]:
    messages = _MESSAGES.get(language or "fr", _MESSAGES["fr"])
    failures = int(values.get("failures") or 0)
    values["failures"] = messages["failures"].format(count=failures) if failures else ""
    title, body = messages[event]
    return title, body.format(**values)


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


async def send(targets: Targets, title: str, message: str, failed: bool = False) -> dict[str, str | None]:
    """Envoie sur chaque canal configuré. Renvoie {canal: erreur ou None}."""
    results: dict[str, str | None] = {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for channel in targets.channels:
            try:
                if channel == "discord":
                    if not is_discord_webhook(targets.discord_webhook or ""):
                        raise ValueError("URL de webhook Discord invalide")
                    resp = await client.post(
                        targets.discord_webhook,
                        json={
                            "username": "Analysarr",
                            "embeds": [{"title": title, "description": message, "color": 0xEF4444 if failed else 0x10B981}],
                        },
                    )
                elif channel == "ntfy":
                    headers = {"Authorization": f"Bearer {targets.ntfy_token}"} if targets.ntfy_token else {}
                    resp = await client.post(
                        targets.ntfy_url,
                        content=message.encode("utf-8"),
                        # Titre en paramètre d'URL (encodé UTF-8) : un en-tête HTTP
                        # ne transporte pas proprement les accents.
                        params={"title": title, "priority": "high" if failed else "default"},
                        headers=headers,
                    )
                else:
                    resp = await client.post(
                        f"{targets.gotify_url.rstrip('/')}/message",
                        json={"title": title, "message": message, "priority": 8 if failed else 5},
                        headers={"X-Gotify-Key": targets.gotify_token},
                    )
                resp.raise_for_status()
                results[channel] = None
            except (httpx.HTTPError, ValueError) as exc:
                results[channel] = _safe_error(exc) if isinstance(exc, httpx.HTTPError) else str(exc)
                logger.warning("Notification %s non envoyée : %s", channel, results[channel])
    return results


def notify(settings: Settings | None, event: str, failed: bool = False, **values: object) -> None:
    """Déclenche une notification en tâche de fond si l'événement est activé
    et qu'au moins un canal est configuré. Ne lève jamais d'exception."""
    if settings is None or event not in _EVENT_SETTING:
        return
    preference = _EVENT_SETTING[event]
    if preference is not None and not getattr(settings, preference, False):
        return
    targets = targets_from(settings)
    if not targets.channels:
        return
    title, message = render(settings.language, event, **values)
    try:
        task = asyncio.get_running_loop().create_task(send(targets, title, message, failed))
    except RuntimeError:
        return  # hors boucle asyncio (ne devrait pas arriver dans l'application)
    _pending.add(task)
    task.add_done_callback(_pending.discard)
