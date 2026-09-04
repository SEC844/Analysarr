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
    PathsRead,
    QbittorrentRead,
    ServiceApiKeyRead,
    SettingsRead,
    SettingsWrite,
)
from app.services.connection_test import TESTERS

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
            emby=ServiceApiKeyRead(),
            sonarr=ServiceApiKeyRead(),
            radarr=ServiceApiKeyRead(),
            qbittorrent=QbittorrentRead(),
            paths=PathsRead(),
            cross_seed=CrossSeedRead(),
        )

    return SettingsRead(
        configured=_is_configured(s),
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

    row.updated_at = datetime.now(timezone.utc)

    session.add(row)
    session.commit()
    session.refresh(row)
    return _to_read(row)


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
