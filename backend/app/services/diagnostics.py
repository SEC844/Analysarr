import os
import stat as stat_module

from sqlmodel import Session, select

from app.clients.emby import EmbyClient
from app.clients.qbittorrent import QbittorrentAuthError, QbittorrentClient
from app.models.media import Torrent
from app.models.settings import Settings
from app.schemas.diagnostics import (
    DiagnosticsResult,
    EmbyFileDebug,
    PathCheck,
    PathDiagnostics,
    PathStat,
    TorrentDebug,
    TorrentFileDebug,
    UnmatchedTorrent,
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


def _common_unresolved_prefix(checks: list[PathCheck]) -> str | None:
    """Dossier commun à tous les chemins non résolus — révèle d'un coup
    d'œil un point de montage manquant (ex : un disque/partage dédié à une
    catégorie de torrents jamais ajouté au conteneur Analysarr), plutôt que
    de laisser l'utilisateur repérer le motif lui-même dans une liste de
    dizaines de chemins individuels. N'est retourné que si ce dossier est
    SPÉCIFIQUE aux chemins en échec (aucun chemin résolu ne s'y trouve
    aussi) — sinon ce n'est qu'un ancêtre commun sans rapport avec la
    panne (ex : "/data" partagé par tout le monde) et l'afficher induirait
    en erreur plutôt que d'aider."""
    unresolved_paths = [c.path for c in checks if not c.resolved and c.path]
    if len(unresolved_paths) < 2:
        return None
    try:
        common = os.path.commonpath(unresolved_paths)
    except ValueError:
        return None
    if not common or common in ("/", os.path.sep):
        return None
    prefix_with_sep = common.rstrip("/\\") + "/"
    if any(c.resolved and c.path and c.path.startswith(prefix_with_sep) for c in checks):
        return None
    return common


def _build_diag(checks: list[PathCheck]) -> PathDiagnostics:
    resolved = sum(1 for c in checks if c.resolved)
    unresolved_samples = [c for c in checks if not c.resolved][:MAX_SAMPLES]
    return PathDiagnostics(
        total=len(checks),
        resolved=resolved,
        unresolved_samples=unresolved_samples,
        common_unresolved_prefix=_common_unresolved_prefix(checks),
    )


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
                save_path = t.get("save_path")
                content_path = t.get("content_path")
                # Comme scan.py : un torrent multi-fichiers (pack saison, film
                # avec extras) a un content_path qui est un DOSSIER, jamais un
                # fichier régulier — stat_inode() le rejette systématiquement
                # par construction. Se limiter à content_path/save_path fait
                # donc passer "non résolu" des torrents que le scan matche en
                # réalité très bien via ses fichiers individuels. On reproduit
                # ici exactement ce que fait le scan : résolu si AU MOINS UN
                # fichier du torrent est accessible.
                try:
                    files = await qbit.get_files(t["hash"])
                except Exception:  # noqa: BLE001 - repli silencieux, comme le scan
                    files = []
                file_paths = [os.path.join(save_path, f["name"]) for f in files if f.get("name") and save_path]
                if not file_paths and content_path:
                    file_paths = [content_path]
                resolved = any(stat_inode(p) is not None for p in file_paths)
                qbit_checks.append(
                    PathCheck(
                        label=t.get("name", t.get("hash", "?")),
                        path=file_paths[0] if file_paths else (content_path or save_path),
                        resolved=resolved,
                    )
                )
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


async def list_unmatched_torrents(session: Session, settings: Settings) -> list[UnmatchedTorrent]:
    """Torrents présents dans qBittorrent mais absents de la base après le
    dernier scan (`Torrent.media_id` n'existe que pour un torrent rattaché à
    un média) — le complément exact de `qbittorrent_matched_count` /
    `qbittorrent_torrent_count` affiché après chaque scan. Sert à savoir
    CONCRÈTEMENT quels torrents échappent aux trois passes de rattachement
    (inode, historique Sonarr/Radarr, similarité de titre), plutôt que de se
    contenter du chiffre agrégé."""
    matched_hashes = {h.lower() for h in session.exec(select(Torrent.hash)).all()}

    async with QbittorrentClient(
        settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password
    ) as qbit:
        torrents = await qbit.get_torrents()

    return [
        UnmatchedTorrent(hash=t["hash"], name=t.get("name", ""), save_path=t.get("save_path"))
        for t in torrents
        if t["hash"].lower() not in matched_hashes
    ]


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


async def debug_emby_movies(settings: Settings, title_contains: str) -> list[EmbyFileDebug]:
    """Détaille, pour les films Emby dont le titre contient `title_contains`,
    le chemin et l'inode/device réels du fichier — pour comparer directement
    contre ceux calculés par debug_torrents() et déterminer si un torrent
    donné est vraiment sur le même système de fichiers que la bibliothèque
    (`device` identique) ou non, sans deviner."""
    needle = title_contains.lower()
    emby = EmbyClient(settings.emby_url, settings.emby_api_key)
    movies = await emby.get_library_items("Movie")
    matches = [m for m in movies if needle in m.get("Name", "").lower()][:MAX_SERIES_MATCHES]

    results: list[EmbyFileDebug] = []
    for movie in matches:
        for source in _media_sources(movie):
            path = source.get("Path")
            if not path:
                continue
            results.append(EmbyFileDebug(item_name=movie.get("Name", "?"), episode_label=None, stat=_stat_path(path)))

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
