"""Notifications vers Discord, ntfy et Gotify — toutes optionnelles.

- Contenu : titre, média concerné (jaquette en pièce jointe quand le canal
  le permet), espace libéré, réussites/échecs et détail de chaque étape ;
  résumé chiffré pour les scans.
- Au mieux : un canal injoignable ne fait jamais échouer un scan ni une
  suppression (envoi en tâche de fond, erreurs journalisées).
- Secrets : l'URL d'un webhook Discord ou un jeton ntfy/Gotify donne le droit
  d'écrire dans le canal — jamais journalisés ni renvoyés tels quels (les
  messages d'erreur httpx contiennent l'URL complète : seuls le code HTTP ou
  le type d'erreur sont conservés).
- Discord : seules les URL officielles de webhook sont acceptées, pour ne pas
  transformer Analysarr en relais HTTP vers n'importe quelle adresse.
- Jaquette : jamais d'URL externe, les octets de l'image (cache disque, sinon
  serveur multimédia) sont envoyés en pièce jointe, et seulement si leur type
  est une image connue (`safe_image_type`)."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol

import httpx

from app.clients.emby import EmbyClient, media_server_client
from app.models.settings import Settings
from app.services.poster_cache import read_cached_poster, safe_image_type

if TYPE_CHECKING:
    from app.services.action_log import MediaRef

logger = logging.getLogger("analysarr.notifications")

_TIMEOUT = 15.0
_MAX_POSTER_BYTES = 8 * 1024 * 1024
_MAX_DETAIL_LINES = 10
# Références fortes vers les envois en cours (sinon le ramasse-miettes peut
# annuler une tâche asyncio avant sa fin).
_pending: set[asyncio.Task] = set()

# Icône du projet (même dépôt que le code), pour l'avatar Discord.
ICON_URL = "https://raw.githubusercontent.com/SEC844/Analysarr/main/unraid/analysarr.png"

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

_COLORS = {"success": 0x10B981, "warning": 0xF59E0B, "error": 0xEF4444, "info": 0x0EA5E9}
_NTFY_TAGS = {"success": "white_check_mark", "warning": "warning", "error": "x", "info": "information_source"}
_NTFY_PRIORITY = {"success": "default", "warning": "high", "error": "high", "info": "default"}
_GOTIFY_PRIORITY = {"success": 5, "warning": 7, "error": 8, "info": 5}
_IMAGE_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}

_TEXT = {
    "fr": {
        "colon": " : ",
        "units": ("o", "Ko", "Mo", "Go", "To"),
        "movie": "Film",
        "series": "Série",
        "delete_selection": "Suppression effectuée",
        "cascade_delete": "Nettoyage effectué",
        "hardlink_repair": "Hardlinks réparés",
        "type": "Type",
        "freed": "Espace libéré",
        "succeeded": "Réussites",
        "failed": "Échecs",
        "details": "Détail",
        "more": "… et {count} autre(s)",
        "scan_completed": "Scan terminé",
        "scan_summary": "La bibliothèque a été analysée.",
        "scan_failed": "Échec du scan",
        "media": "Médias",
        "duplicates": "Doublons",
        "orphans": "Orphelins",
        "reclaimable": "Espace récupérable",
        "matched": "Torrents rattachés",
        "duration": "Durée",
        "test": "Notification de test",
        "test_body": "Les notifications d'Analysarr fonctionnent : résumés de scan et actions effectuées arriveront ici.",
    },
    "en": {
        "colon": ": ",
        "units": ("B", "KB", "MB", "GB", "TB"),
        "movie": "Movie",
        "series": "Series",
        "delete_selection": "Deletion completed",
        "cascade_delete": "Cleanup completed",
        "hardlink_repair": "Hardlinks repaired",
        "type": "Type",
        "freed": "Space freed",
        "succeeded": "Succeeded",
        "failed": "Failed",
        "details": "Details",
        "more": "… and {count} more",
        "scan_completed": "Scan completed",
        "scan_summary": "The library has been analyzed.",
        "scan_failed": "Scan failed",
        "media": "Media",
        "duplicates": "Duplicates",
        "orphans": "Orphans",
        "reclaimable": "Reclaimable space",
        "matched": "Matched torrents",
        "duration": "Duration",
        "test": "Test notification",
        "test_body": "Analysarr notifications are working: scan summaries and performed actions will show up here.",
    },
}


class Step(Protocol):
    label: str
    success: bool
    error: str | None


@dataclass
class Notification:
    """Message indépendant du canal : chaque canal le met en forme (embed
    Discord, texte ntfy, Markdown Gotify)."""

    title: str
    description: str
    level: str = "info"  # success | warning | error | info
    fields: list[tuple[str, str]] = field(default_factory=list)
    details_label: str = ""
    details: list[str] = field(default_factory=list)
    colon: str = ": "
    image: tuple[bytes, str] | None = None


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


def notification_language(settings: Settings | None) -> str:
    return "en" if settings is not None and settings.language == "en" else "fr"


# ---- Contenu -----------------------------------------------------------------


def format_bytes(value: int, language: str) -> str:
    text = _TEXT[language]
    size, unit = float(value), 0
    while size >= 1024 and unit < len(text["units"]) - 1:
        size /= 1024
        unit += 1
    # Même format que l'interface (lib/format.ts::formatBytes).
    number = f"{size:.0f}" if unit == 0 else f"{size:.1f}"
    return f"{number} {text['units'][unit]}"


def _format_duration(seconds: int) -> str:
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def _shorten(value: str, limit: int = 90) -> str:
    # Les libellés sont souvent des chemins : la fin (nom du fichier) est la
    # partie utile.
    return value if len(value) <= limit else "…" + value[-(limit - 1) :]


def _detail_lines(steps: list[Step], more: str) -> list[str]:
    lines = [
        ("✅ " if s.success else "❌ ") + _shorten(s.label) + (f" — {_shorten(s.error, 120)}" if s.error else "")
        for s in steps[:_MAX_DETAIL_LINES]
    ]
    if len(steps) > _MAX_DETAIL_LINES:
        lines.append(more.format(count=len(steps) - _MAX_DETAIL_LINES))
    return lines


def action_notification(
    language: str,
    action: str,
    media: "MediaRef",
    *,
    success: int,
    failures: int,
    freed_bytes: int | None,
    steps: list[Step],
) -> Notification:
    text = _TEXT[language]
    fields = [(text["type"], text["series"] if media.media_type == "series" else text["movie"])]
    if freed_bytes:
        fields.append((text["freed"], format_bytes(freed_bytes, language)))
    fields.append((text["succeeded"], str(success)))
    if failures:
        fields.append((text["failed"], str(failures)))
    return Notification(
        title=text[action],
        description=f"{media.title} ({media.year})" if media.year else media.title,
        level="success" if not failures else "warning" if success else "error",
        fields=fields,
        details_label=text["details"],
        details=_detail_lines(steps, text["more"]),
        colon=text["colon"],
    )


def scan_completed_notification(
    language: str,
    *,
    media: int,
    duplicates: int,
    orphans: int,
    reclaimable_bytes: int,
    matched: int,
    torrents: int,
    duration_seconds: int | None,
) -> Notification:
    text = _TEXT[language]
    fields = [
        (text["media"], str(media)),
        (text["duplicates"], str(duplicates)),
        (text["orphans"], str(orphans)),
        (text["reclaimable"], format_bytes(reclaimable_bytes, language)),
        (text["matched"], f"{matched}/{torrents}"),
    ]
    if duration_seconds is not None:
        fields.append((text["duration"], _format_duration(duration_seconds)))
    level = "warning" if duplicates or orphans else "success"
    return Notification(
        title=text["scan_completed"], description=text["scan_summary"], level=level, fields=fields, colon=text["colon"]
    )


def scan_failed_notification(language: str, error: str) -> Notification:
    text = _TEXT[language]
    return Notification(title=text["scan_failed"], description=_shorten(error, 500), level="error", colon=text["colon"])


def build_test_notification(language: str) -> Notification:
    text = _TEXT[language]
    return Notification(title=text["test"], description=text["test_body"], level="info", colon=text["colon"])


async def load_poster(client: EmbyClient | None, media: "MediaRef") -> tuple[bytes, str] | None:
    """Jaquette du média : cache disque d'abord (toujours présent si la fiche
    a été affichée, y compris après suppression du média), sinon serveur
    multimédia. None si indisponible — la notification part sans image."""
    if not media.emby_item_id:
        return None
    cached = read_cached_poster(media.emby_item_id, media.poster_image_tag)
    if cached is None and client is not None:
        try:
            cached = await client.fetch_poster(media.emby_item_id)
        except httpx.HTTPError:
            cached = None
    if cached is None:
        return None
    content, content_type = cached[0], safe_image_type(cached[1])
    if content_type is None or not content or len(content) > _MAX_POSTER_BYTES:
        return None
    return content, content_type


# ---- Envoi -------------------------------------------------------------------


def _details_text(n: Notification, limit: int) -> str:
    joined = "\n".join(n.details)
    return joined if len(joined) <= limit else joined[: limit - 1] + "…"


def _plain_text(n: Notification, limit: int) -> str:
    parts = [n.description, "\n".join(f"{name}{n.colon}{value}" for name, value in n.fields)]
    if n.details:
        parts.append(f"{n.details_label}\n{_details_text(n, limit)}")
    joined = "\n\n".join(p for p in parts if p)
    return joined if len(joined) <= limit else joined[: limit - 1] + "…"


def _markdown(n: Notification) -> str:
    parts = [n.description, "  \n".join(f"**{name}**{n.colon}{value}" for name, value in n.fields)]
    if n.details:
        parts.append(f"**{n.details_label}**\n\n" + "\n".join(f"- {line}" for line in n.details))
    return "\n\n".join(p for p in parts if p)


async def _send_discord(client: httpx.AsyncClient, url: str, n: Notification) -> httpx.Response:
    if not is_discord_webhook(url):
        raise ValueError("URL de webhook Discord invalide")
    embed: dict = {
        "title": n.title[:256],
        "description": n.description[:4096],
        "color": _COLORS[n.level],
        "fields": [{"name": name[:256], "value": value[:1024], "inline": True} for name, value in n.fields],
        "footer": {"text": "Analysarr"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if n.details:
        embed["fields"].append({"name": n.details_label, "value": _details_text(n, 1024), "inline": False})
    payload = {"username": "Analysarr", "avatar_url": ICON_URL, "embeds": [embed]}
    if n.image is None:
        return await client.post(url, json=payload)
    content, content_type = n.image
    filename = f"poster.{_IMAGE_EXT.get(content_type, 'jpg')}"
    embed["thumbnail"] = {"url": f"attachment://{filename}"}
    return await client.post(
        url, data={"payload_json": json.dumps(payload)}, files={"files[0]": (filename, content, content_type)}
    )


async def _send_ntfy(client: httpx.AsyncClient, url: str, token: str | None, n: Notification) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # Titre et message en paramètres d'URL (encodés UTF-8) : un en-tête HTTP ne
    # transporte pas proprement les accents.
    params = {"title": n.title, "priority": _NTFY_PRIORITY[n.level], "tags": _NTFY_TAGS[n.level]}
    if n.image is not None:
        content, content_type = n.image
        filename = f"poster.{_IMAGE_EXT.get(content_type, 'jpg')}"
        resp = await client.put(
            url, content=content, params={**params, "message": _plain_text(n, 1500), "filename": filename}, headers=headers
        )
        # Serveur ntfy sans stockage des pièces jointes (option désactivée par
        # défaut en auto-hébergé) : on renvoie le message sans image.
        if resp.status_code != 400:
            return resp
    return await client.post(url, content=_plain_text(n, 4000).encode("utf-8"), params=params, headers=headers)


async def _send_gotify(client: httpx.AsyncClient, url: str, token: str, n: Notification) -> httpx.Response:
    return await client.post(
        f"{url.rstrip('/')}/message",
        json={
            "title": n.title,
            "message": _markdown(n),
            "priority": _GOTIFY_PRIORITY[n.level],
            "extras": {"client::display": {"contentType": "text/markdown"}},
        },
        headers={"X-Gotify-Key": token},
    )


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return type(exc).__name__
    return str(exc)


async def send(targets: Targets, notification: Notification) -> dict[str, str | None]:
    """Envoie sur chaque canal configuré. Renvoie {canal: erreur ou None}."""
    results: dict[str, str | None] = {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for channel in targets.channels:
            try:
                if channel == "discord":
                    resp = await _send_discord(client, targets.discord_webhook or "", notification)
                elif channel == "ntfy":
                    resp = await _send_ntfy(client, targets.ntfy_url or "", targets.ntfy_token, notification)
                else:
                    resp = await _send_gotify(client, targets.gotify_url or "", targets.gotify_token or "", notification)
                resp.raise_for_status()
                results[channel] = None
            except (httpx.HTTPError, ValueError) as exc:
                results[channel] = _safe_error(exc)
                logger.warning("Notification %s non envoyée : %s", channel, results[channel])
    return results


def notify(
    settings: Settings | None, event: str, notification: Notification, poster: "MediaRef | None" = None
) -> None:
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
    # Client créé maintenant : les réglages ne sont plus lisibles une fois la
    # session de base fermée.
    client = media_server_client(settings) if poster is not None else None

    async def deliver() -> None:
        if poster is not None:
            notification.image = await load_poster(client, poster)
        await send(targets, notification)

    try:
        task = asyncio.get_running_loop().create_task(deliver())
    except RuntimeError:
        return  # hors boucle asyncio (ne devrait pas arriver dans l'application)
    _pending.add(task)
    task.add_done_callback(_pending.discard)
