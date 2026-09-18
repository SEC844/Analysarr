"""Notifications multi-canaux vers Discord, ntfy et Gotify — toutes optionnelles.

- Chaque canal est une entrée indépendante (`NotificationChannel`) avec son
  adresse, son jeton et SA liste d'événements : plusieurs webhooks Discord
  peuvent coexister, par exemple un pour les scans et un autre pour les
  suppressions.
- Contenu : titre, média concerné (jaquette en pièce jointe quand le canal le
  permet), espace libéré, réussites/échecs et détail de chaque étape ; résumé
  chiffré pour les scans.
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
from sqlmodel import Session, select

from app.clients.emby import EmbyClient, media_server_client
from app.models.notification_channel import NotificationChannel
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

CHANNEL_KINDS = ("discord", "ntfy", "gotify")
MAX_CHANNELS = 20

# Événements notifiables, chacun activable canal par canal.
NOTIFICATION_EVENTS = (
    "scan_completed",
    "scan_failed",
    "orphan_detected",
    "duplicate_detected",
    "non_hardlink_detected",
    "delete_selection",
    "cascade_delete",
    "hardlink_repair",
    "cross_seed_search",
    "automation",
)
# Sélection par défaut d'un nouveau canal : ce qui demande une action ou
# signale un problème, jamais le simple résumé de scan (trop fréquent).
DEFAULT_EVENTS = (
    "scan_failed",
    "orphan_detected",
    "duplicate_detected",
    "delete_selection",
    "cascade_delete",
    "hardlink_repair",
)

DISCORD_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
    "https://ptb.discord.com/api/webhooks/",
    "https://canary.discord.com/api/webhooks/",
)

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
        "cross_seed_search": "Recherche cross-seed",
        "automation": "Automatisation exécutée",
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
        "orphan_detected": "Nouveaux torrents orphelins",
        "orphan_detected_summary": "Des torrents ne protègent plus aucun fichier de la bibliothèque.",
        "duplicate_detected": "Nouveaux doublons",
        "duplicate_detected_summary": "Plusieurs fichiers existent pour un même film ou épisode.",
        "non_hardlink_detected": "Nouveaux torrents non hardlinkés",
        "non_hardlink_detected_summary": "Le contenu est bien seedé, mais sans hardlink vers la bibliothèque.",
        "affected_media": "Médias concernés",
        "test": "Notification de test",
        "test_body": "Les notifications d'Analysarr fonctionnent : les événements choisis pour ce canal arriveront ici.",
        "rule": "Règle",
        "trigger": "Déclencheur",
    },
    "en": {
        "colon": ": ",
        "units": ("B", "KB", "MB", "GB", "TB"),
        "movie": "Movie",
        "series": "Series",
        "delete_selection": "Deletion completed",
        "cascade_delete": "Cleanup completed",
        "hardlink_repair": "Hardlinks repaired",
        "cross_seed_search": "Cross-seed search",
        "automation": "Automation ran",
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
        "orphan_detected": "New orphan torrents",
        "orphan_detected_summary": "Some torrents no longer protect any library file.",
        "duplicate_detected": "New duplicates",
        "duplicate_detected_summary": "Several files exist for the same movie or episode.",
        "non_hardlink_detected": "New non-hardlinked torrents",
        "non_hardlink_detected_summary": "The content is seeded, but without a hardlink to the library.",
        "affected_media": "Media affected",
        "test": "Test notification",
        "test_body": "Analysarr notifications are working: the events selected for this channel will show up here.",
        "rule": "Rule",
        "trigger": "Trigger",
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
class ChannelTarget:
    """Copie des réglages d'un canal au moment de l'événement : l'envoi se fait
    en tâche de fond, après la fermeture de la session de base."""

    id: int
    kind: str
    name: str
    url: str
    token: str | None
    events: tuple[str, ...]

    def wants(self, event: str) -> bool:
        return event in self.events


def channel_events(channel: NotificationChannel) -> tuple[str, ...]:
    try:
        events = json.loads(channel.events or "[]")
    except ValueError:
        return ()
    return tuple(event for event in events if event in NOTIFICATION_EVENTS)


def channel_targets(session: Session, only_enabled: bool = True) -> list[ChannelTarget]:
    query = select(NotificationChannel).order_by(NotificationChannel.id)
    if only_enabled:
        query = query.where(NotificationChannel.enabled == True)  # noqa: E712 - SQLModel n'accepte pas `is True`
    return [
        ChannelTarget(
            id=row.id, kind=row.kind, name=row.name, url=row.url, token=row.token, events=channel_events(row)
        )
        for row in session.exec(query).all()
    ]


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


def media_label(language: str, media_type: str) -> str:
    text = _TEXT[language]
    return text["series"] if media_type == "series" else text["movie"]


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
    fields = [(text["type"], media_label(language, media.media_type))]
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


def detection_notification(language: str, event: str, medias: list[tuple[str, int]]) -> Notification:
    """Nouveau constat d'un scan : orphelins, doublons ou torrents non
    hardlinkés apparus depuis le scan précédent. `medias` : (titre, espace
    récupérable)."""
    text = _TEXT[language]
    total = sum(size for _, size in medias)
    fields = [
        (text["affected_media"], str(len(medias))),
        (text["reclaimable"], format_bytes(total, language)),
    ]
    details = [f"• {_shorten(title)} — {format_bytes(size, language)}" for title, size in medias[:_MAX_DETAIL_LINES]]
    if len(medias) > _MAX_DETAIL_LINES:
        details.append(text["more"].format(count=len(medias) - _MAX_DETAIL_LINES))
    return Notification(
        title=text[event],
        description=text[f"{event}_summary"],
        level="warning",
        fields=fields,
        details_label=text["details"],
        details=details,
        colon=text["colon"],
    )


def automation_notification(
    language: str, rule_name: str, trigger: str, steps: list[Step], *, freed_bytes: int | None = None
) -> Notification:
    text = _TEXT[language]
    failures = sum(1 for s in steps if not s.success)
    fields = [(text["rule"], rule_name), (text["trigger"], trigger)]
    if freed_bytes:
        fields.append((text["freed"], format_bytes(freed_bytes, language)))
    fields.append((text["succeeded"], str(len(steps) - failures)))
    if failures:
        fields.append((text["failed"], str(failures)))
    return Notification(
        title=text["automation"],
        description=rule_name,
        level="success" if not failures else "warning",
        fields=fields,
        details_label=text["details"],
        details=_detail_lines(steps, text["more"]),
        colon=text["colon"],
    )


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


async def send(targets: list[ChannelTarget], notification: Notification) -> dict[str, str | None]:
    """Envoie sur chaque canal donné. Renvoie {nom du canal: erreur ou None}."""
    results: dict[str, str | None] = {}
    if not targets:
        return results
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for target in targets:
            try:
                if target.kind == "discord":
                    resp = await _send_discord(client, target.url, notification)
                elif target.kind == "ntfy":
                    resp = await _send_ntfy(client, target.url, target.token, notification)
                else:
                    resp = await _send_gotify(client, target.url, target.token or "", notification)
                resp.raise_for_status()
                results[target.name] = None
            except (httpx.HTTPError, ValueError) as exc:
                results[target.name] = _safe_error(exc)
                logger.warning("Notification %s non envoyée : %s", target.name, results[target.name])
    return results


def notify(
    targets: list[ChannelTarget],
    event: str,
    notification: Notification,
    poster: "MediaRef | None" = None,
    settings: Settings | None = None,
) -> None:
    """Déclenche une notification en tâche de fond sur les canaux abonnés à cet
    événement. Ne lève jamais d'exception."""
    if event not in NOTIFICATION_EVENTS:
        return
    wanted = [target for target in targets if target.wants(event)]
    if not wanted:
        return
    # Client créé maintenant : les réglages ne sont plus lisibles une fois la
    # session de base fermée.
    client = media_server_client(settings) if poster is not None else None

    async def deliver() -> None:
        if poster is not None:
            notification.image = await load_poster(client, poster)
        await send(wanted, notification)

    try:
        task = asyncio.get_running_loop().create_task(deliver())
    except RuntimeError:
        return  # hors boucle asyncio (ne devrait pas arriver dans l'application)
    _pending.add(task)
    task.add_done_callback(_pending.discard)
