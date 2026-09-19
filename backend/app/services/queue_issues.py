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
    Un téléchargement qui progresse normalement n'est jamais retenu.

    Rattachement volontairement strict : SEUL l'identifiant que Sonarr/Radarr
    donne lui-même à l'entrée compte. Une entrée sans identifiant de média
    (téléchargement qu'ils ne reconnaissent pas, `movieId: 0` des « unknown
    items ») est ignorée plutôt que rattachée au petit bonheur — un import
    raté affiché sur la fiche d'un autre média serait pire que pas d'info du
    tout. Aucun repli par titre, par chemin ou par hash n'est fait ici,
    contrairement au rattachement des torrents.
    """
    issues: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        arr_id = record.get(key)
        # bool est un int en Python : `True` ne doit pas passer pour un id.
        if not isinstance(arr_id, int) or isinstance(arr_id, bool) or arr_id <= 0:
            continue
        if _kind(record) is None:
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
                output_path=str(record.get("outputPath") or "") or None,
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
    entry: dict[str, Any] = {"path": path}
    # Un champ à null fait échouer la commande côté Sonarr/Radarr (« Quality is
    # required ») : on n'envoie que ce que le serveur a lui-même renseigné.
    for key in ("quality", "languages", "releaseGroup", "downloadId", "folderName", "indexerFlags"):
        value = candidate.get(key)
        if value not in (None, "", []):
            entry[key] = value
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


async def retry_import(
    client: ArrClient, download_id: str | None, is_series: bool, output_path: str | None = None
) -> tuple[int, list[str]]:
    """Relance l'import d'un téléchargement. Renvoie (fichiers envoyés, motifs
    de refus). `download_id` et `output_path` viennent tous deux de la file
    d'attente de Sonarr/Radarr, jamais de l'utilisateur.

    Trois tentatives dans l'ordre : par téléchargement, puis par dossier de
    sortie (le `downloadId` n'est plus connu dès que l'entrée quitte la file),
    puis, si Sonarr/Radarr ne propose toujours rien, une relance de sa tâche
    « traiter les téléchargements » — le geste que ferait l'utilisateur dans
    l'interface de Sonarr/Radarr."""
    candidates: list[dict[str, Any]] = []
    if download_id:
        found = await client.manual_import_candidates(download_id=download_id)
        candidates = found if isinstance(found, list) else []
    if not candidates and output_path:
        found = await client.manual_import_candidates(folder=output_path)
        candidates = found if isinstance(found, list) else []
    if not candidates:
        # Rien à importer manuellement : on demande au serveur de repasser sur
        # sa file. Sans fichier proposé, c'est la seule action utile.
        await client.process_monitored_downloads()
        return 0, ["aucun fichier proposé à l'import ; traitement de la file relancé côté Sonarr/Radarr"]

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


def _error_text(exc: httpx.HTTPError) -> str:
    """Message d'erreur utile : le code HTTP ET ce que Sonarr/Radarr explique
    dans le corps de la réponse (tronqué), plutôt qu'un « 400 Bad Request »
    opaque. Aucune clé API n'y transite : elle voyage dans un en-tête."""
    response = getattr(exc, "response", None)
    if response is None:
        return f"{type(exc).__name__} : {exc}"
    body = " ".join((response.text or "").split())
    return f"HTTP {response.status_code}{' — ' + body[:200] if body else ''}"


async def safe_retry_import(
    client: ArrClient, download_id: str | None, is_series: bool, output_path: str | None = None
) -> tuple[int, list[str], str | None]:
    """Variante qui ne lève jamais : l'erreur réseau est renvoyée en texte."""
    try:
        imported, rejections = await retry_import(client, download_id, is_series, output_path)
    except httpx.HTTPError as exc:
        return 0, [], _error_text(exc)
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
        if not issue.download_id and not issue.output_path:
            steps.append(
                DeleteStepResult(
                    kind="import_retry",
                    label=label,
                    success=False,
                    error="Téléchargement inconnu de Sonarr/Radarr : import à relancer depuis leur interface.",
                )
            )
            continue
        imported, rejections, error = await safe_retry_import(
            client, issue.download_id, is_series, issue.output_path
        )
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
