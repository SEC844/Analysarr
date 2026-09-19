"""Problèmes de file d'attente Sonarr/Radarr, de deux natures :

- `import` : le fichier est téléchargé mais Sonarr/Radarr n'a pas réussi à le
  ranger dans la bibliothèque (statut `import_rate`, relance possible) ;
- `stalled` : le téléchargement lui-même est en souffrance — bloqué, sans
  source, ou en erreur côté client (statut `telechargement_bloque`, purement
  informatif : Analysarr ne supprime jamais et ne relance rien pour ça, c'est
  le rôle de Cleanuparr ou Decluttarr).

La file d'attente (`/api/v3/queue`) est la seule source : elle porte l'état de
suivi (`trackedDownloadState`), le statut du téléchargement (`status`) et les
motifs (`statusMessages`), exactement ce qu'affiche l'onglet Activité de
Sonarr/Radarr. Elle couvre aussi Usenet, là où l'état du client torrent
n'aurait rien dit.

Dans les deux cas, le média n'est plus signalé « absent » et son
téléchargement n'est jamais proposé au nettoyage : il est en cours de route.
"""

from typing import TYPE_CHECKING, Any

import httpx
from sqlmodel import select

from app.clients.arr import ArrClient
from app.models.media import ImportIssue, MediaType

if TYPE_CHECKING:
    from sqlmodel import Session

    from app.models.media import Media
    from app.models.settings import Settings

__all__ = [
    "BLOCKED_STATES",
    "IMPORT_KIND",
    "STALLED_KIND",
    "execute_import_retry",
    "index_queue_issues",
    "issue_rows_for",
    "retry_import",
]

IMPORT_KIND = "import"
STALLED_KIND = "stalled"

# États de suivi qui signalent un import à débloquer. `importPending` seul ne
# suffit pas : c'est l'attente normale juste après un téléchargement, sans
# problème — il n'est retenu que si Sonarr/Radarr signale aussi un avertissement
# ou une erreur.
BLOCKED_STATES = {"importblocked", "importfailed", "failedpending", "failed"}
_WARNING_STATUSES = {"warning", "error"}
_MAX_REASON = 400


def _reason(record: dict[str, Any]) -> str:
    messages: list[str] = []
    for entry in record.get("statusMessages") or []:
        title = (entry.get("title") or "").strip()
        for message in entry.get("messages") or []:
            message = (message or "").strip()
            if message and message not in messages:
                messages.append(message)
        if not (entry.get("messages") or []) and title and title not in messages:
            messages.append(title)
    if not messages and record.get("errorMessage"):
        messages.append(str(record["errorMessage"]).strip())
    return " · ".join(messages)[:_MAX_REASON]


def _is_blocked(record: dict[str, Any]) -> bool:
    state = str(record.get("trackedDownloadState") or "").lower()
    status = str(record.get("trackedDownloadStatus") or "").lower()
    if state in BLOCKED_STATES:
        return True
    # Import en attente accompagné d'un avertissement : c'est le cas le plus
    # courant (« One or more episodes expected in this release were not
    # imported or missing »).
    return state == "importpending" and status in _WARNING_STATUSES


def _is_stalled(record: dict[str, Any]) -> bool:
    """Téléchargement qui n'avance plus. Sonarr/Radarr signale lui-même le cas
    (« The download is stalled with no connections ») en passant le statut de
    la ligne en warning/error, ou en renseignant `errorMessage`."""
    state = str(record.get("trackedDownloadState") or "").lower()
    if state not in ("", "downloading"):
        return False
    status = str(record.get("status") or "").lower()
    tracked = str(record.get("trackedDownloadStatus") or "").lower()
    return status in {"warning", "error", "failed"} or tracked in {"warning", "error"} or bool(record.get("errorMessage"))


def _kind(record: dict[str, Any]) -> str | None:
    if _is_blocked(record):
        return IMPORT_KIND
    return STALLED_KIND if _is_stalled(record) else None


def _episode_label(record: dict[str, Any]) -> str:
    episode = record.get("episode") or {}
    season = episode.get("seasonNumber")
    number = episode.get("episodeNumber")
    if season is None or number is None:
        return ""
    return f"S{int(season):02d}E{int(number):02d}"


def index_queue_issues(records: list[dict[str, Any]], key: str) -> dict[int, list[dict[str, Any]]]:
    """Entrées problématiques de la file, indexées par `movieId` ou `seriesId`.
    Un téléchargement qui progresse normalement n'est jamais retenu."""
    issues: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        arr_id = record.get(key)
        if not isinstance(arr_id, int) or _kind(record) is None:
            continue
        issues.setdefault(arr_id, []).append(record)
    return issues


def issue_rows_for(records: list[dict[str, Any]]) -> list[ImportIssue]:
    rows: list[ImportIssue] = []
    for record in records:
        download_id = record.get("downloadId")
        rows.append(
            ImportIssue(
                media_id=0,
                kind=_kind(record) or STALLED_KIND,
                queue_id=record.get("id") if isinstance(record.get("id"), int) else None,
                download_id=str(download_id) if download_id else None,
                title=str(record.get("title") or "")[:300],
                state=str(record.get("trackedDownloadState") or ""),
                reason=_reason(record),
                size=int(record["size"]) if isinstance(record.get("size"), (int, float)) else None,
                episode_label=_episode_label(record),
            )
        )
    return rows


def _import_file(candidate: dict[str, Any], is_series: bool) -> dict[str, Any] | None:
    """Traduit un candidat de `/api/v3/manualimport` en entrée de commande
    ManualImport. Renvoie None si Sonarr/Radarr n'a rattaché le fichier à
    aucun média : importer « au hasard » n'a aucun sens et rangerait un
    fichier au mauvais endroit."""
    path = candidate.get("path")
    if not path:
        return None
    entry: dict[str, Any] = {
        "path": path,
        "quality": candidate.get("quality"),
        "languages": candidate.get("languages"),
        "releaseGroup": candidate.get("releaseGroup"),
        "downloadId": candidate.get("downloadId"),
    }
    if is_series:
        series_id = (candidate.get("series") or {}).get("id")
        episode_ids = [e.get("id") for e in candidate.get("episodes") or [] if e.get("id")]
        if not series_id or not episode_ids:
            return None
        entry["seriesId"] = series_id
        entry["episodeIds"] = episode_ids
        if candidate.get("seasonNumber") is not None:
            entry["seasonNumber"] = candidate["seasonNumber"]
    else:
        movie_id = (candidate.get("movie") or {}).get("id")
        if not movie_id:
            return None
        entry["movieId"] = movie_id
    return entry


def _blocking_rejections(candidate: dict[str, Any]) -> list[str]:
    """Motifs de refus définitifs. Un refus temporaire (fichier encore en
    cours d'écriture) disparaît tout seul : il ne bloque pas la tentative."""
    reasons: list[str] = []
    for rejection in candidate.get("rejections") or []:
        kind = str(rejection.get("type") or "permanent").lower()
        if kind == "permanent":
            reason = str(rejection.get("reason") or "").strip()
            if reason:
                reasons.append(reason)
    return reasons


async def retry_import(client: ArrClient, download_id: str, is_series: bool) -> tuple[int, list[str]]:
    """Relance l'import d'un téléchargement. Renvoie (fichiers envoyés,
    motifs de refus). Ne demande QUE les fichiers de ce téléchargement :
    `download_id` vient de la file d'attente de Sonarr/Radarr, jamais de
    l'utilisateur."""
    candidates = await client.manual_import_candidates(download_id)
    if not isinstance(candidates, list):
        return 0, []

    files: list[dict[str, Any]] = []
    rejections: list[str] = []
    for candidate in candidates:
        blocking = _blocking_rejections(candidate)
        if blocking:
            rejections.extend(blocking)
            continue
        entry = _import_file(candidate, is_series)
        if entry is None:
            rejections.append("fichier non rattaché à un média par Sonarr/Radarr")
            continue
        files.append(entry)

    if files:
        await client.manual_import(files)
    return len(files), list(dict.fromkeys(rejections))


async def safe_retry_import(client: ArrClient, download_id: str, is_series: bool) -> tuple[int, list[str], str | None]:
    """Variante qui ne lève jamais : l'erreur réseau est renvoyée en texte."""
    try:
        imported, rejections = await retry_import(client, download_id, is_series)
    except httpx.HTTPError as exc:
        return 0, [], str(exc)
    return imported, rejections, None


async def execute_import_retry(session: "Session", media: "Media", settings: "Settings") -> tuple[list[Any], int]:
    """Relance l'import de tous les téléchargements bloqués d'un média.
    Renvoie (étapes, nombre de fichiers envoyés à l'import). Les statuts ne
    sont pas recalculés ici : Sonarr/Radarr importe en tâche de fond, c'est le
    prochain scan qui constate le résultat."""
    from app.schemas.media import DeleteStepResult  # import différé : évite un cycle
    from app.services.arr_instances import arr_target_for

    issues = [
        issue
        for issue in session.exec(select(ImportIssue).where(ImportIssue.media_id == media.id)).all()
        # Un téléchargement en souffrance n'a rien à importer : il n'est pas
        # encore arrivé. Seuls les imports bloqués se relancent.
        if issue.kind == IMPORT_KIND
    ]
    steps: list[DeleteStepResult] = []
    if not issues:
        return steps, 0

    target = arr_target_for(session, settings, media)
    if target is None:
        steps.append(
            DeleteStepResult(kind="import_retry", label=media.title, success=False, error="Instance introuvable.")
        )
        return steps, 0

    is_series = media.media_type == MediaType.series
    client: ArrClient = target.sonarr() if is_series else target.radarr()

    imported_total = 0
    for issue in issues:
        label = issue.title or media.title
        if not issue.download_id:
            steps.append(
                DeleteStepResult(
                    kind="import_retry",
                    label=label,
                    success=False,
                    error="Téléchargement inconnu du client torrent : import à relancer depuis Sonarr/Radarr.",
                )
            )
            continue
        imported, rejections, error = await safe_retry_import(client, issue.download_id, is_series)
        imported_total += imported
        if error is not None:
            steps.append(DeleteStepResult(kind="import_retry", label=label, success=False, error=error))
        elif imported:
            steps.append(
                DeleteStepResult(kind="import_retry", label=f"{label} — {imported} fichier(s) importé(s)", success=True)
            )
        else:
            steps.append(
                DeleteStepResult(
                    kind="import_retry",
                    label=label,
                    success=False,
                    error=" · ".join(rejections)[:_MAX_REASON] or "Aucun fichier importable.",
                )
            )
    return steps, imported_total
