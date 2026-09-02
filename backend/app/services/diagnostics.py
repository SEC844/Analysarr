from app.clients.emby import EmbyClient
from app.clients.qbittorrent import QbittorrentAuthError, QbittorrentClient
from app.models.settings import Settings
from app.schemas.diagnostics import DiagnosticsResult, PathCheck, PathDiagnostics
from app.services.hardlink import stat_inode
from app.services.scan import _media_sources

MAX_SAMPLES = 25


def _build_diag(checks: list[PathCheck]) -> PathDiagnostics:
    resolved = sum(1 for c in checks if c.resolved)
    unresolved = [c for c in checks if not c.resolved][:MAX_SAMPLES]
    return PathDiagnostics(total=len(checks), resolved=resolved, unresolved_samples=unresolved)


async def run_diagnostics(settings: Settings) -> DiagnosticsResult:
    """Vérifie, en direct (sans passer par le cache du scan), si les chemins
    renvoyés par qBittorrent et Emby sont réellement accessibles (os.stat)
    depuis le conteneur Analysarr — la précondition silencieuse dont dépend
    toute la détection par hardlink."""

    qbit_checks: list[PathCheck] = []
    try:
        async with QbittorrentClient(
            settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password
        ) as qbit:
            torrents = await qbit.get_torrents()
        for t in torrents:
            path = t.get("content_path") or t.get("save_path")
            qbit_checks.append(PathCheck(label=t.get("name", t.get("hash", "?")), path=path, resolved=stat_inode(path) is not None))
    except QbittorrentAuthError as exc:
        raise RuntimeError(f"Authentification qBittorrent refusée : {exc}") from exc

    emby_checks: list[PathCheck] = []
    emby = EmbyClient(settings.emby_url, settings.emby_api_key)
    movies = await emby.get_library_items("Movie")
    for m in movies:
        for source in _media_sources(m):
            path = source.get("Path")
            emby_checks.append(PathCheck(label=m.get("Name", "?"), path=path, resolved=stat_inode(path) is not None))

    return DiagnosticsResult(qbittorrent=_build_diag(qbit_checks), emby=_build_diag(emby_checks))
