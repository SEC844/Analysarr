"""Webhooks Sonarr/Radarr : Analysarr est prévenu à chaque import, mise à
niveau, suppression ou ajout, au lieu d'attendre le scan suivant.

Branchement AUTOMATIQUE (`wanted_webhooks`, appliqué par le superviseur) :
dès qu'une instance est configurée et que l'adresse d'Analysarr est connue,
la notification est créée dans Sonarr/Radarr par leur API, à partir de LEUR
modèle (`/api/v3/notification/schema`) — seuls les événements que la version
connaît sont cochés. Un webhook « Analysarr » déjà présent (même adresse) est
réutilisé plutôt que dupliqué ; une instance déplacée (autre adresse) ou une
adresse d'Analysarr modifiée le fait rebrancher.

Sécurité de la route publique `/api/webhooks/{service}/{instance}` :
- secret propre à chaque instance, généré ici, présenté par Sonarr/Radarr en
  authentification Basic (champs Username/Password de leur webhook, vérifiés
  dans `WebhookSettings`) — jamais dans l'URL, donc jamais dans un journal ;
- seule l'empreinte sha256 est conservée, comparée en temps constant ;
- le contenu reçu n'est qu'un SIGNAL : on n'en retient que le type
  d'événement (liste fermée) et l'identifiant du film ou de la série, puis
  la vérité est relue par l'API (`media_rescan`).

Rebranchement : Sonarr/Radarr teste le nouveau secret pendant l'enregistrement
(vérifié dans `ProviderControllerBase.CreateProvider`) ; l'ancien reste
accepté tant que l'opération n'a pas abouti. Ce test est un appel de LEUR
conteneur vers Analysarr : tant qu'Analysarr n'écoute pas (démarrage) ou que
l'adresse ne mène pas jusqu'à lui, l'enregistrement reste suspendu jusqu'à
leur propre délai. D'où un branchement toujours en tâche de fond, jamais
pendant le démarrage, et un message qui nomme la vraie cause.

Désactivables (`Settings.arr_webhooks_enabled`) : Analysarr retire alors ses
webhooks de chez eux, et le suivi en lecture seule (`watchers.ArrWatcher`)
reste seul."""

import base64
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlmodel import Session, col, select

from app.clients.arr import ArrClient, http_error_text
from app.models.arr_webhook import ArrWebhook
from app.models.settings import Settings
from app.schemas.realtime import ArrService
from app.services.arr_instances import ArrTarget, arr_targets

logger = logging.getLogger(__name__)

WEBHOOK_NAME = "Analysarr"
WEBHOOK_USERNAME = "analysarr"
ARR_SERVICES: tuple[ArrService, ...] = ("sonarr", "radarr")

# Événements qui changent un média (cochés s'ils existent dans la version de
# Sonarr/Radarr). Santé et mises à jour de l'application : sans objet.
EVENT_FLAGS = {
    "sonarr": (
        "onGrab",
        "onDownload",
        "onUpgrade",
        "onImportComplete",
        "onRename",
        "onSeriesAdd",
        "onSeriesDelete",
        "onEpisodeFileDelete",
        "onEpisodeFileDeleteForUpgrade",
        "onManualInteractionRequired",
    ),
    "radarr": (
        "onGrab",
        "onDownload",
        "onUpgrade",
        "onRename",
        "onMovieAdded",
        "onMovieDelete",
        "onMovieFileDelete",
        "onMovieFileDeleteForUpgrade",
        "onManualInteractionRequired",
    ),
}

# Types d'événement reçus qui demandent une nouvelle analyse du média
# (`eventType` des charges Sonarr/Radarr). `Test` ne fait que confirmer la
# liaison ; tout le reste est ignoré.
RESCAN_EVENTS = frozenset(
    {
        "Grab",
        "Download",
        "Rename",
        "SeriesAdd",
        "SeriesDelete",
        "EpisodeFileDelete",
        "MovieAdded",
        "MovieDelete",
        "MovieFileDelete",
        "ManualInteractionRequired",
        "ImportComplete",
    }
)
TEST_EVENT = "Test"

# Enregistrement d'une notification : Sonarr/Radarr attend la réponse de son
# propre test (un appel vers Analysarr) avant de répondre.
SAVE_TIMEOUT = 60.0
# Un événement livré par webhook n'est pas réanalysé une seconde fois quand
# le suivi en lecture seule retrouve LE MÊME événement dans l'historique.
DELIVERY_MEMORY_SECONDS = 600.0

# Nature d'un événement, côté webhook (`eventType` de la charge) et côté
# historique (`eventType` de `/api/v3/history`, vérifié dans
# `EpisodeHistoryEventType` / `MovieHistoryEventType`) : seuls ces événements
# existent des deux côtés.
WEBHOOK_EVENT_KINDS = {
    "Grab": "grab",
    "Download": "import",
    "EpisodeFileDelete": "delete",
    "MovieFileDelete": "delete",
    "Rename": "rename",
}
HISTORY_EVENT_KINDS = {
    "grabbed": "grab",
    "downloadFolderImported": "import",
    "seriesFolderImported": "import",
    "movieFolderImported": "import",
    "episodeFileDeleted": "delete",
    "movieFileDeleted": "delete",
    "episodeFileRenamed": "rename",
    "movieFileRenamed": "rename",
}

# Événement : (service, instance, nature, téléchargement ou fiche).
EventKey = tuple[str, int, str, str]

# En mémoire, modifiés dans la boucle asyncio uniquement : dernier appel reçu
# de chaque instance ; événements reçus en attente de l'analyse de leur média
# (par clé de média), puis confirmés une fois l'analyse réussie.
_received_at: dict[tuple[str, int], float] = {}
_awaiting: dict[str, list[tuple[EventKey, float]]] = {}
_delivered_at: dict[EventKey, float] = {}
# Instances dont Sonarr/Radarr a suspendu le webhook d'Analysarr (voir
# suspended_by_service) : plus rien n'arrive par là.
_suspended: set[tuple[str, int]] = set()


class WebhookError(RuntimeError):
    """Branchement, test ou débranchement refusé : message à afficher tel quel."""


def source_key(service: str, instance_id: int) -> str:
    return f"webhook:{service}:{instance_id}"


def normalize_base_url(raw: str) -> str:
    """Adresse d'Analysarr vue depuis Sonarr/Radarr : http(s), sans
    identifiants, chemin éventuel (reverse-proxy en sous-dossier), jamais de
    paramètre ni de fragment."""
    url = raw.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebhookError("Adresse d'Analysarr invalide : http:// ou https:// suivi d'un hôte.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise WebhookError("Adresse d'Analysarr invalide : ni identifiants, ni paramètres.")
    return url


def webhook_url(base_url: str, service: str, instance_id: int) -> str:
    return f"{normalize_base_url(base_url)}/api/webhooks/{service}/{instance_id}"


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def note_received(service: str, instance_id: int) -> None:
    """Appel authentifié reçu de cette instance (test compris) : il prouve
    qu'elle joint Analysarr."""
    _received_at[(service, instance_id)] = time.monotonic()


def event_key(
    service: str, instance_id: int, kind: str | None, download_id: Any, arr_id: int | None
) -> EventKey | None:
    """Un événement précis : son téléchargement quand il en a un (grab et
    import d'une même release partagent le téléchargement, pas la nature),
    sinon sa fiche. Bug réel : le dédoublonnage par fiche seule faisait
    écarter l'import d'une série parce que son GRAB était arrivé par webhook."""
    if kind is None:
        return None
    if isinstance(download_id, str) and download_id:
        return (service, instance_id, kind, f"download:{download_id.lower()}")
    if arr_id is None:
        return None
    return (service, instance_id, kind, f"arr:{arr_id}")


def note_delivery(media_key: str, key: EventKey | None) -> None:
    """Événement reçu par webhook pour ce média : il ne compte comme livré
    qu'une fois l'analyse du média réussie (`confirm_deliveries`). Si elle
    échoue, le suivi en lecture seule le reprendra depuis l'historique."""
    if key is None:
        return
    now = time.monotonic()
    _awaiting.setdefault(media_key, []).append((key, now))
    for media in list(_awaiting):
        kept = [(k, at) for k, at in _awaiting[media] if now - at <= DELIVERY_MEMORY_SECONDS]
        if kept:
            _awaiting[media] = kept
        else:
            del _awaiting[media]


def confirm_deliveries(media_key: str) -> None:
    """Analyse du média réussie : ses événements reçus sont livrés."""
    now = time.monotonic()
    for key, _received in _awaiting.pop(media_key, []):
        _delivered_at[key] = now
    for key in [k for k, at in _delivered_at.items() if now - at > DELIVERY_MEMORY_SECONDS]:
        del _delivered_at[key]


def recently_delivered(key: EventKey | None) -> bool:
    if key is None:
        return False
    service, instance_id = key[0], key[1]
    if (service, instance_id) in _suspended:
        return False  # webhook coupé : le suivi en lecture seule ne doit rien écarter
    at = _delivered_at.get(key)
    return at is not None and time.monotonic() - at <= DELIVERY_MEMORY_SECONDS


def forget_deliveries() -> None:
    _received_at.clear()
    _awaiting.clear()
    _delivered_at.clear()
    _suspended.clear()


def mark_suspended(service: str, instance_id: int, suspended: bool) -> bool:
    """Retient l'état ; vrai si la suspension vient de commencer."""
    key = (service, instance_id)
    started = suspended and key not in _suspended
    if suspended:
        _suspended.add(key)
    else:
        _suspended.discard(key)
    return started


def is_suspended(service: str, instance_id: int) -> bool:
    return (service, instance_id) in _suspended


# Contrôle de santé de Sonarr/Radarr qui signale une notification coupée
# (vérifié dans leur code, `NotificationStatusCheck`) : après 5 min d'échecs
# d'envoi, la notification est désactivée par paliers (1 min à 24 h) et plus
# rien n'est envoyé. Avertissement « … : nom1, nom2 » quand une partie des
# notifications est coupée ; erreur sans aucun nom quand elles le sont toutes.
NOTIFICATION_HEALTH_SOURCE = "NotificationStatusCheck"


def suspended_in(health: list[dict[str, Any]], name: str = WEBHOOK_NAME) -> bool:
    """Le bilan de santé désigne-t-il ce webhook ? Message traduit selon la
    langue de leur interface : seuls les noms (jamais traduits) sont lus."""
    for check in health:
        if not isinstance(check, dict) or check.get("source") != NOTIFICATION_HEALTH_SOURCE:
            continue
        if str(check.get("type", "")).lower() == "error":
            return True  # toutes les notifications coupées, celle d'Analysarr comprise
        message = str(check.get("message") or "")
        names = message.rsplit(":", 1)[-1] if ":" in message else ""
        if name in (part.strip() for part in names.split(",")):
            return True
    return False


def suspended_message(target: ArrTarget) -> str:
    return (
        f"{target.name} a suspendu le webhook d'Analysarr après des échecs d'envoi : il n'envoie plus rien "
        "jusqu'à son prochain essai. Le suivi en lecture seule prend le relais ; vérifiez que "
        f"{target.name} joint toujours Analysarr."
    )


async def suspended_by_service(target: ArrTarget) -> bool:
    """Sonarr/Radarr a-t-il coupé le webhook d'Analysarr ? Une erreur réseau
    remonte (rien n'est prouvé)."""
    return suspended_in(await _client(target).get_health())


# --- Réception ------------------------------------------------------------------


def presented_secret(authorization: str | None) -> str | None:
    """Mot de passe de l'en-tête `Authorization: Basic …` (identifiant
    `analysarr`), ou None."""
    if not authorization or not authorization.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(authorization[6:].strip(), validate=True).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    return password if sep and username == WEBHOOK_USERNAME and password else None


def secret_matches(row: ArrWebhook, secret: str) -> bool:
    """Comparaison en temps constant avec le secret en place ET, pendant un
    rebranchement, le nouveau."""
    digest = _digest(secret)
    ok = hmac.compare_digest(digest, row.secret_hash)
    if row.pending_secret_hash is not None:
        ok = hmac.compare_digest(digest, row.pending_secret_hash) or ok
    return ok


@dataclass(frozen=True)
class WebhookEvent:
    event_type: str
    # Film ou série concernés (identifiant Sonarr/Radarr), s'il y en a un.
    arr_id: int | None
    # Téléchargement concerné (grab, import), s'il y en a un.
    download_id: str | None = None


def parse_event(service: str, body: Any) -> WebhookEvent | None:
    """Seuls le type d'événement et l'identifiant sont lus : rien d'autre
    dans la charge n'est cru. Charge illisible = None."""
    if not isinstance(body, dict) or not isinstance(body.get("eventType"), str):
        return None
    holder = body.get("series" if service == "sonarr" else "movie")
    raw_id = holder.get("id") if isinstance(holder, dict) else None
    arr_id = raw_id if isinstance(raw_id, int) and not isinstance(raw_id, bool) and raw_id > 0 else None
    download_id = body.get("downloadId")
    return WebhookEvent(body["eventType"], arr_id, download_id if isinstance(download_id, str) else None)


# --- Branchement ----------------------------------------------------------------


def _client(target: ArrTarget) -> ArrClient:
    return target.sonarr() if target.kind == "sonarr" else target.radarr()


def _template(schema: list[dict[str, Any]]) -> dict[str, Any]:
    template = next((t for t in schema if t.get("implementation") == "Webhook"), None)
    if template is None:
        raise WebhookError("Cette version de Sonarr/Radarr ne propose pas de webhook.")
    return template


def _events(service: str, template: dict[str, Any]) -> list[str]:
    return [flag for flag in EVENT_FLAGS[service] if flag in template]


def _field_value(notification: dict[str, Any], name: str) -> Any:
    return next((f.get("value") for f in notification.get("fields") or [] if f.get("name") == name), None)


def _body(template: dict[str, Any], service: str, url: str, secret: str, existing_id: int | None) -> dict[str, Any]:
    values = {"url": url, "method": 1, "username": WEBHOOK_USERNAME, "password": secret}
    body = {key: value for key, value in template.items() if key != "presets"}
    body.update(
        {
            "name": WEBHOOK_NAME,
            "enable": True,
            "tags": [],
            "fields": [
                {**field, "value": values[field["name"]]} if field.get("name") in values else field
                for field in template.get("fields") or []
            ],
        }
    )
    for flag in EVENT_FLAGS[service]:
        if flag in template:
            body[flag] = True
    if existing_id is not None:
        body["id"] = existing_id
    else:
        body.pop("id", None)
    return body


def target_signature(target: ArrTarget) -> str:
    """Empreinte de l'adresse de l'instance (jamais de sa clé) : une instance
    déplacée n'a plus le webhook créé ailleurs."""
    return hashlib.sha256(target.url.rstrip("/").encode()).hexdigest()


def is_connected(row: ArrWebhook | None, target: ArrTarget, base_url: str, instance_id: int) -> bool:
    """Webhook en place ET à jour : même adresse d'Analysarr, même instance."""
    if row is None or row.notification_id is None or row.target_signature != target_signature(target):
        return False
    try:
        return row.url == webhook_url(base_url, target.kind, instance_id)
    except WebhookError:
        return False


@dataclass(frozen=True)
class WantedWebhook:
    target: ArrTarget
    instance_id: int  # 0 = instance principale

    @property
    def key(self) -> str:
        return source_key(self.target.kind, self.instance_id)


def wanted_webhooks(session: Session, settings: Settings | None) -> list[WantedWebhook]:
    """Un webhook par instance Sonarr/Radarr configurée."""
    return [
        WantedWebhook(target, target.instance_id or 0)
        for service in ARR_SERVICES
        for target in arr_targets(session, settings, service)
    ]


def forget_orphans(session: Session, wanted: list[WantedWebhook]) -> None:
    """Lignes d'instances qui n'existent plus (retirées dans les réglages, où
    le webhook a déjà été débranché chez elles). Lu toutes les 5 s : n'écrit
    que s'il y a quelque chose à retirer."""
    keep = {(w.target.kind, w.instance_id) for w in wanted}
    orphans = [row for row in session.exec(select(ArrWebhook)).all() if (row.service, row.instance_id) not in keep]
    for row in orphans:
        session.delete(row)
    if orphans:
        session.commit()


def find_row(session: Session, service: str, instance_id: int) -> ArrWebhook | None:
    return session.exec(
        select(ArrWebhook).where(col(ArrWebhook.service) == service, col(ArrWebhook.instance_id) == instance_id)
    ).first()


async def register(session: Session, target: ArrTarget, base_url: str, instance_id: int) -> ArrWebhook:
    """Crée ou met à jour le webhook dans Sonarr/Radarr. Le nouveau secret
    est accepté AVANT l'appel (Sonarr/Radarr le teste pour enregistrer)."""
    url = webhook_url(base_url, target.kind, instance_id)
    client = _client(target)
    secret = secrets.token_urlsafe(32)
    row = find_row(session, target.kind, instance_id)
    created = row is None
    if row is None:
        row = ArrWebhook(service=target.kind, instance_id=instance_id, secret_hash=_digest(secret), url=url)
    # Instance déplacée : la notification connue vit sur l'ancienne adresse.
    known_id = row.notification_id if row.target_signature == target_signature(target) else None
    row.pending_secret_hash = _digest(secret)
    session.add(row)
    session.commit()
    try:
        template = _template(await client.get_notification_schema())
        existing_id = known_id or _existing_id(await client.get_notifications(), url)
    except (httpx.HTTPError, WebhookError) as exc:
        _abandon(session, row, created)
        raise WebhookError(_failure(target, exc, base_url)) from exc
    asked_at = time.monotonic()
    try:
        body = _body(template, target.kind, url, secret, existing_id)
        saved = await client.save_notification(body, timeout=SAVE_TIMEOUT)
    except httpx.TimeoutException as exc:
        # Sonarr/Radarr peut encore l'enregistrer après coup : le nouveau
        # secret reste accepté, et le prochain essai retrouve ce webhook par
        # son adresse au lieu d'en créer un second.
        reached = _received_at.get((target.kind, instance_id), -1.0) >= asked_at
        raise WebhookError(_timeout_failure(target, base_url, reached=reached)) from exc
    except httpx.HTTPError as exc:
        _abandon(session, row, created)
        raise WebhookError(_failure(target, exc, base_url)) from exc
    row.notification_id = saved.get("id") if isinstance(saved.get("id"), int) else existing_id
    row.secret_hash, row.pending_secret_hash, row.url = _digest(secret), None, url
    row.target_signature = target_signature(target)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _failure(target: ArrTarget, exc: Exception, base_url: str) -> str:
    """Injoignable (réseau) ou refusé (réponse d'erreur, test du webhook
    raté) : deux causes, deux remèdes différents."""
    if isinstance(exc, httpx.HTTPError) and not isinstance(exc, httpx.HTTPStatusError):
        return f"{target.name} injoignable : {http_error_text(exc)}"
    detail = http_error_text(exc) if isinstance(exc, httpx.HTTPError) else str(exc)
    message = f"{target.name} a refusé le webhook : {detail}"
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 400:
        # Leur test du webhook a échoué : presque toujours l'adresse.
        message += f" Vérifiez que {base_url} est joignable depuis {target.name}."
    return message


def _timeout_failure(target: ArrTarget, base_url: str, *, reached: bool) -> str:
    """Sonarr/Radarr a répondu aux lectures mais pas à l'enregistrement : il
    attendait son propre test, c'est-à-dire un appel vers Analysarr."""
    if reached:
        return f"{target.name} joint bien Analysarr mais n'a pas confirmé le webhook à temps : nouvel essai bientôt."
    return (
        f"{target.name} ne parvient pas à joindre Analysarr à {base_url} : son test du webhook n'aboutit pas. "
        f"Indiquez une adresse joignable depuis le conteneur de {target.name}."
    )


def _existing_id(notifications: list[dict[str, Any]], url: str) -> int | None:
    """Webhook Analysarr déjà présent (même adresse) : réutilisé, jamais dupliqué."""
    for notification in notifications:
        if notification.get("implementation") == "Webhook" and _field_value(notification, "url") == url:
            notification_id = notification.get("id")
            return notification_id if isinstance(notification_id, int) else None
    return None


def _abandon(session: Session, row: ArrWebhook, created: bool) -> None:
    if created:
        session.delete(row)
    else:
        row.pending_secret_hash = None
        session.add(row)
    session.commit()


async def still_registered(target: ArrTarget, row: ArrWebhook) -> bool:
    """Le webhook existe-t-il toujours chez Sonarr/Radarr (retiré à la main
    dans leur interface) ? Seul un 404 dit non ; une erreur réseau remonte."""
    if row.notification_id is None:
        return False
    try:
        await _client(target).get_notification(row.notification_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return False
        raise
    return True


async def send_test(target: ArrTarget, row: ArrWebhook) -> None:
    """Demande à Sonarr/Radarr d'envoyer son événement de test : prouve que
    LEUR conteneur joint Analysarr avec le bon secret. Le mot de passe masqué
    renvoyé par l'API est remplacé par la valeur enregistrée chez eux
    (vérifié dans `SchemaBuilder`)."""
    if row.notification_id is None:
        raise WebhookError("Webhook pas encore branché.")
    client = _client(target)
    try:
        notification = await client.get_notification(row.notification_id)
        await client.test_notification(notification)
    except httpx.HTTPError as exc:
        raise WebhookError(f"Test refusé par {target.name} : {http_error_text(exc)}") from exc


async def unregister(session: Session, target: ArrTarget | None, row: ArrWebhook) -> None:
    """Retire le webhook chez Sonarr/Radarr puis ici. Déjà absent chez eux
    (404) : retiré ici quand même. Instance supprimée ou injoignable :
    l'erreur est remontée, rien n'est oublié en silence."""
    if target is not None and row.notification_id is not None:
        try:
            await _client(target).delete_notification(row.notification_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise WebhookError(f"{target.name} a refusé le retrait : {http_error_text(exc)}") from exc
        except httpx.HTTPError as exc:
            raise WebhookError(f"{target.name} injoignable : {http_error_text(exc)}") from exc
    session.delete(row)
    session.commit()
