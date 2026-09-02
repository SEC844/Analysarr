import os
import stat as stat_module

from app.clients.emby import EmbyClient
from app.clients.qbittorrent import QbittorrentAuthError, QbittorrentClient
from app.models.settings import Settings
from app.schemas.diagnostics import (
    DiagnosticsResult,
    EmbyFileDebug,
    PathCheck,
    PathDiagnostics,
    PathStat,
    TorrentDebug,
    TorrentFileDebug,
)
from app.services.hardlink import stat_inode
from app.services.scan import _episode_label, _media_sources

MAX_SAMPLES = 25
MAX_TORRENT_MATCHES = 5
MAX_FILES_PER_TORRENT = 500
MAX_SERIES_MATCHES = 3


def _stat_path(path: str) -> PathStat:
    exists = False
    is_regular = False
    inode = None
    device = None
    try:
        st = os.stat(path)
        exists = True
        is_regular = stat_module.S_ISREG(st.st_mode)
        if is_regular:
            inode, device = st.st_ino, st.st_dev
    except OSError:
        pass
    return PathStat(path=path, exists=exists, is_regular_file=is_regular, inode=inode, device=device)


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


async def debug_torrents(settings: Settings, name_contains: str) -> list[TorrentDebug]:
    """Interroge qBittorrent en direct pour les torrents dont le nom contient
    `name_contains`, et détaille pour chacun : la réponse brute de
    torrents/files, chaque chemin candidat reconstruit, et son statut de
    résolution (existe / est un fichier régulier / inode). Sert à diagnostiquer
    pourquoi un torrent connu de qBittorrent n'est rattaché à aucun média."""
    needle = name_contains.lower()

    async with QbittorrentClient(
        settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password
    ) as qbit:
        torrents = await qbit.get_torrents()
        matches = [t for t in torrents if needle in t.get("name", "").lower()][:MAX_TORRENT_MATCHES]

        results: list[TorrentDebug] = []
        for t in matches:
            save_path = t.get("save_path")
            content_path = t.get("content_path")
            files_error: str | None = None
            try:
                files = await qbit.get_files(t["hash"])
            except Exception as exc:  # noqa: BLE001 - on veut voir l'erreur telle quelle, pas planter le diagnostic
                files = []
                files_error = f"{type(exc).__name__} : {exc}"

            file_debugs: list[TorrentFileDebug] = []
            candidates = files[:MAX_FILES_PER_TORRENT] if files else [{"name": None}]
            for f in candidates:
                rel = f.get("name")
                resolved_path = os.path.join(save_path, rel) if rel and save_path else (content_path or save_path or "")
                stat_result = _stat_path(resolved_path)
                file_debugs.append(
                    TorrentFileDebug(
                        relative_name=rel,
                        resolved_path=resolved_path,
                        exists=stat_result.exists,
                        is_regular_file=stat_result.is_regular_file,
                        inode=stat_result.inode,
                        device=stat_result.device,
                    )
                )

            results.append(
                TorrentDebug(
                    hash=t["hash"],
                    name=t.get("name", ""),
                    save_path=save_path,
                    content_path=content_path,
                    files_api_count=len(files),
                    files_api_error=files_error,
                    files=file_debugs,
                )
            )

    return results


async def debug_emby_series_files(settings: Settings, title_contains: str) -> list[EmbyFileDebug]:
    """Détaille, pour les séries Emby dont le titre contient `title_contains`,
    le chemin et l'inode réels de chaque fichier d'épisode — pour comparer
    directement contre ceux calculés par debug_torrents() et trouver quel(s)
    torrent(s) sont réellement hardlinkés au fichier actuellement actif."""
    needle = title_contains.lower()
    emby = EmbyClient(settings.emby_url, settings.emby_api_key)
    series_list = await emby.get_library_items("Series")
    matches = [s for s in series_list if needle in s.get("Name", "").lower()][:MAX_SERIES_MATCHES]

    results: list[EmbyFileDebug] = []
    for series in matches:
        episodes = await emby.get_episodes(series["Id"])
        for episode in episodes:
            label = _episode_label(episode)
            for source in _media_sources(episode):
                path = source.get("Path")
                if not path:
                    continue
                results.append(
                    EmbyFileDebug(item_name=series.get("Name", "?"), episode_label=label, stat=_stat_path(path))
                )

    return results
