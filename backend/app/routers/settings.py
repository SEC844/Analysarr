import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.database import get_session
from app.models.settings import Settings
from app.schemas.settings import (
    BrowseEntry,
    BrowseResult,
    ConnectionTestRequest,
    ConnectionTestResult,
    CrossSeedRead,
    NotificationsRead,
    NotificationTestResult,
    PathsRead,
    QbittorrentRead,
    ScheduleRead,
    SeerRead,
    ServiceApiKeyRead,
    SettingsRead,
    SettingsWrite,
    WatchRead,
)
from app.services.connection_test import TESTERS
from app.services.notifications import (
    is_discord_webhook,
    is_http_url,
    notification_language,
    send,
    targets_from,
    build_test_notification,
)
from app.services.scheduler import configure_scan_schedule
from app.services.watch_stats import excluded_user_ids, recompute_all_aggregates

router = APIRouter()


def _get_row(session: Session) -> Settings | None:
    return session.get(Settings, 1)


def _is_configured(s: Settings) -> bool:
    return all(
        [
            s.emby_url,
            s.emby_api_key,
            s.sonarr_url,
            s.sonarr_api_key,
            s.radarr_url,
            s.radarr_api_key,
            s.qbittorrent_url,
            s.qbittorrent_username,
            s.qbittorrent_password,
            s.emby_library_path,
            s.qbittorrent_download_path,
        ]
    )


def _to_read(s: Settings | None) -> SettingsRead:
    if s is None:
        return SettingsRead(
            configured=False,
            watch=WatchRead(),
            emby=ServiceApiKeyRead(),
            sonarr=ServiceApiKeyRead(),
            radarr=ServiceApiKeyRead(),
            qbittorrent=QbittorrentRead(),
            paths=PathsRead(),
            cross_seed=CrossSeedRead(),
            seer=SeerRead(),
            schedule=ScheduleRead(),
        )

    return SettingsRead(
        configured=_is_configured(s),
        media_server="jellyfin" if s.media_server == "jellyfin" else "emby",
        notifications=NotificationsRead(
            discord_set=bool(s.notify_discord_webhook),
            ntfy_set=bool(s.notify_ntfy_url),
            ntfy_token_set=bool(s.notify_ntfy_token),
            gotify_url=s.notify_gotify_url,
            gotify_token_set=bool(s.notify_gotify_token),
            on_scan=s.notify_on_scan,
            on_scan_failure=s.notify_on_scan_failure,
            on_actions=s.notify_on_actions,
        ),
        watch=WatchRead(excluded_emby_user_ids=sorted(excluded_user_ids(s))),
        emby=ServiceApiKeyRead(url=s.emby_url, api_key_set=bool(s.emby_api_key)),
        sonarr=ServiceApiKeyRead(url=s.sonarr_url, api_key_set=bool(s.sonarr_api_key)),
        radarr=ServiceApiKeyRead(url=s.radarr_url, api_key_set=bool(s.radarr_api_key)),
        qbittorrent=QbittorrentRead(
            url=s.qbittorrent_url,
            username=s.qbittorrent_username,
            password_set=bool(s.qbittorrent_password),
        ),
        paths=PathsRead(
            emby_library_path=s.emby_library_path,
            qbittorrent_download_path=s.qbittorrent_download_path,
        ),
        cross_seed=CrossSeedRead(
            enabled=s.cross_seed_enabled,
            url=s.cross_seed_url,
            api_key_set=bool(s.cross_seed_api_key),
            library_path=s.cross_seed_library_path,
        ),
        seer=SeerRead(enabled=s.seer_enabled, url=s.seer_url, api_key_set=bool(s.seer_api_key)),
        schedule=ScheduleRead(
            enabled=s.scan_schedule_enabled,
            interval_minutes=s.scan_schedule_interval_minutes,
        ),
    )


@router.get("", response_model=SettingsRead)
def get_settings(session: Session = Depends(get_session)) -> SettingsRead:
    return _to_read(_get_row(session))


@router.put("", response_model=SettingsRead)
def put_settings(payload: SettingsWrite, session: Session = Depends(get_session)) -> SettingsRead:
    row = _get_row(session)
    if row is None:
        row = Settings(id=1)
        session.add(row)

    # Champs non sensibles : toujours remplacés par la valeur envoyée.
    row.media_server = payload.media_server
    row.emby_url = payload.emby_url
    row.sonarr_url = payload.sonarr_url
    row.radarr_url = payload.radarr_url
    row.qbittorrent_url = payload.qbittorrent_url
    row.qbittorrent_username = payload.qbittorrent_username
    row.emby_library_path = payload.emby_library_path
    row.qbittorrent_download_path = payload.qbittorrent_download_path
    row.cross_seed_enabled = payload.cross_seed_enabled
    row.cross_seed_url = payload.cross_seed_url
    row.cross_seed_library_path = payload.cross_seed_library_path
    row.seer_enabled = payload.seer_enabled
    row.seer_url = payload.seer_url
    row.scan_schedule_enabled = payload.scan_schedule_enabled
    row.scan_schedule_interval_minutes = payload.scan_schedule_interval_minutes
    previous_exclusions = excluded_user_ids(row)
    row.excluded_emby_user_ids = json.dumps(sorted(set(payload.excluded_emby_user_ids)))

    # Champs sensibles : une valeur vide/absente conserve la valeur en base
    # (le frontend ne reçoit jamais la vraie clé, donc "vide" veut dire
    # "l'utilisateur n'a rien retapé", pas "effacer la clé").
    if payload.emby_api_key:
        row.emby_api_key = payload.emby_api_key
    if payload.sonarr_api_key:
        row.sonarr_api_key = payload.sonarr_api_key
    if payload.radarr_api_key:
        row.radarr_api_key = payload.radarr_api_key
    if payload.qbittorrent_password:
        row.qbittorrent_password = payload.qbittorrent_password
    if payload.cross_seed_api_key:
        row.cross_seed_api_key = payload.cross_seed_api_key
    if payload.seer_api_key:
        row.seer_api_key = payload.seer_api_key

    _apply_notifications(row, payload)

    row.updated_at = datetime.now(timezone.utc)

    session.add(row)
    session.commit()
    session.refresh(row)

    configure_scan_schedule(row.scan_schedule_interval_minutes if row.scan_schedule_enabled else None)
    if excluded_user_ids(row) != previous_exclusions:
        recompute_all_aggregates(session, row)

    return _to_read(row)


def _apply_notifications(row: Settings, payload: SettingsWrite) -> None:
    if payload.notify_discord_webhook and not is_discord_webhook(payload.notify_discord_webhook):
        raise HTTPException(400, "L'URL Discord doit être une URL de webhook Discord (https://discord.com/api/webhooks/…).")
    for url in (payload.notify_ntfy_url, payload.notify_gotify_url):
        if url and not is_http_url(url):
            raise HTTPException(400, "Les URL ntfy et Gotify doivent commencer par http:// ou https://.")

    row.notify_on_scan = payload.notify_on_scan
    row.notify_on_scan_failure = payload.notify_on_scan_failure
    row.notify_on_actions = payload.notify_on_actions
    if payload.notify_gotify_url is not None:
        row.notify_gotify_url = payload.notify_gotify_url or None
    # Secrets : même règle que les clés API (vide = inchangé).
    if payload.notify_discord_webhook:
        row.notify_discord_webhook = payload.notify_discord_webhook
    if payload.notify_ntfy_url:
        row.notify_ntfy_url = payload.notify_ntfy_url
    if payload.notify_ntfy_token:
        row.notify_ntfy_token = payload.notify_ntfy_token
    if payload.notify_gotify_token:
        row.notify_gotify_token = payload.notify_gotify_token
    if "discord" in payload.notify_clear:
        row.notify_discord_webhook = None
    if "ntfy" in payload.notify_clear:
        row.notify_ntfy_url = row.notify_ntfy_token = None
    if "gotify" in payload.notify_clear:
        row.notify_gotify_url = row.notify_gotify_token = None


@router.post("/notifications/test", response_model=NotificationTestResult)
async def test_notifications(session: Session = Depends(get_session)) -> NotificationTestResult:
    """Envoie une notification de test sur chaque canal ENREGISTRÉ (jamais sur
    une URL fournie dans la requête : pas de relais vers une adresse arbitraire)."""
    settings = session.get(Settings, 1)
    targets = targets_from(settings)
    if not targets.channels:
        raise HTTPException(400, "Aucun canal de notification enregistré.")
    return NotificationTestResult(results=await send(targets, build_test_notification(notification_language(settings))))


@router.post("/test/{service}", response_model=ConnectionTestResult)
async def test_connection(service: str, payload: ConnectionTestRequest) -> ConnectionTestResult:
    tester = TESTERS.get(service)
    if tester is None:
        raise HTTPException(status_code=404, detail=f"Service inconnu : {service}")
    return await tester(payload)


@router.get("/browse", response_model=BrowseResult)
def browse_filesystem(path: str = Query("/", description="Chemin absolu à parcourir")) -> BrowseResult:
    """Liste les sous-dossiers d'un chemin, vu depuis le conteneur Analysarr —
    permet de choisir un chemin de bibliothèque/téléchargement en cliquant
    plutôt qu'en le tapant à l'aveugle (façon navigateur de fichiers Unraid).
    Uniquement des dossiers : ces champs de réglages ne servent qu'à choisir
    un point de montage, jamais un fichier précis."""
    normalized = os.path.normpath(path) if path else "/"
    if not os.path.isabs(normalized):
        raise HTTPException(400, "Le chemin doit être absolu.")
    if not os.path.isdir(normalized):
        raise HTTPException(404, f"Dossier introuvable : {normalized}")

    try:
        names = sorted(os.listdir(normalized), key=str.lower)
    except PermissionError as exc:
        raise HTTPException(403, f"Accès refusé : {exc}") from exc
    except OSError as exc:
        raise HTTPException(400, str(exc)) from exc

    directories: list[BrowseEntry] = []
    for name in names:
        full = os.path.join(normalized, name)
        try:
            if os.path.isdir(full):
                directories.append(BrowseEntry(name=name, path=full))
        except OSError:
            continue  # lien symbolique cassé ou inaccessible : on l'ignore plutôt que d'échouer toute la liste

    parent = os.path.dirname(normalized.rstrip("/\\")) if normalized not in ("/", os.path.sep) else None
    return BrowseResult(path=normalized, parent=parent or None, directories=directories)
