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

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from sqlmodel import select

from app.clients.arr import ArrClient, http_error_text
from app.models.media import ImportIssue, MediaType
from app.schemas.media import DeleteStepResult
from app.services.arr_instances import arr_target_for
from app.services.realtime.hub import hub

if TYPE_CHECKING:
    from sqlmodel import Session

    from app.models.media import Media
    from app.models.settings import Settings

__all__ = [
    "BLOCKED_STATES",
    "IMPORT_KIND",
    "OVERRIDABLE_REJECTIONS",
    "STALLED_KIND",
    "ImportAttempt",
    "execute_import_retry",
    "index_queue_issues",
    "issue_rows_for",
    "prepare_import",
    "triage_candidates",
    "wait_for_command",
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
        for raw in entry.get("messages") or []:
            message = (raw or "").strip()
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
    return (
        status in {"warning", "error", "failed"} or tracked in {"warning", "error"} or bool(record.get("errorMessage"))
    )


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


# --- Relance d'un import bloqué -----------------------------------------------
#
# Reproduit l'« Import manuel » de Sonarr/Radarr, vérifié dans leur code
# (`ManualImportService`) : les fichiers proposés par `GET /manualimport`
# portent les refus de Sonarr/Radarr, et la commande `ManualImport` importe ce
# qu'on lui envoie SANS repasser ces refus — cocher un fichier refusé dans leur
# interface, c'est passer outre. Analysarr ne passe outre que les refus sans
# risque ci-dessous ; les autres protègent quelque chose (un meilleur fichier
# déjà en place, un échantillon avéré, un rattachement d'épisode douteux) et
# restent à trancher dans Sonarr/Radarr.

# Refus levés d'office : ils ne disent rien de mal du fichier. Le titre
# d'épisode inconnu ne sert qu'au nommage, et l'échantillon « indéterminé »
# signifie seulement que la durée n'a pas pu être lue (voir SAMPLE_SIZE_RATIO).
OVERRIDABLE_REJECTIONS = (
    "Episode has a TBA title and recently aired",
    "Episode does not have a title and recently aired",
    "Unable to determine if file is a sample",
)
_SAMPLE_INDETERMINATE = "Unable to determine if file is a sample"
# Un échantillon pèse quelques % du fichier principal : un fichier au moins
# moitié aussi gros que le plus gros du téléchargement n'en est pas un.
SAMPLE_SIZE_RATIO = 0.5
# Suivi de la commande ManualImport : assez pour un import ordinaire, assez
# court pour rester sous le délai d'un proxy inverse (60 s par défaut).
COMMAND_POLL_SECONDS = 1.0
COMMAND_TIMEOUT_SECONDS = 25.0
_FINISHED_COMMANDS = {"completed", "failed", "aborted", "cancelled", "orphaned"}
_FORWARDED_FIELDS = ("quality", "languages", "releaseGroup", "downloadId", "folderName", "indexerFlags", "releaseType")


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
    for key in _FORWARDED_FIELDS:
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


def _size(candidate: dict[str, Any]) -> int:
    size = candidate.get("size")
    return int(size) if isinstance(size, (int, float)) and not isinstance(size, bool) else 0


def _is_overridable(reason: str, candidate: dict[str, Any], largest: int) -> bool:
    if not reason.startswith(OVERRIDABLE_REJECTIONS):
        return False
    if reason.startswith(_SAMPLE_INDETERMINATE):
        return largest > 0 and _size(candidate) >= largest * SAMPLE_SIZE_RATIO
    return True


def _rejections(candidate: dict[str, Any], largest: int) -> tuple[list[str], list[str]]:
    """(refus bloquants, refus levés). Un refus temporaire (fichier encore en
    cours d'écriture) disparaît tout seul : il ne bloque pas la tentative."""
    blocking: list[str] = []
    overridden: list[str] = []
    for rejection in candidate.get("rejections") or []:
        if str(rejection.get("type") or "permanent").lower() != "permanent":
            continue
        reason = str(rejection.get("reason") or "").strip()
        if not reason:
            continue
        (overridden if _is_overridable(reason, candidate, largest) else blocking).append(reason)
    return blocking, overridden


@dataclass(slots=True)
class ImportAttempt:
    """Ce qu'une relance a pu préparer pour un téléchargement : fichiers à
    importer, refus levés d'office, refus bloquants."""

    files: list[dict[str, Any]] = field(default_factory=list)
    overridden: list[str] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)
    # Aucun fichier proposé : la file a été relancée côté Sonarr/Radarr.
    relaunched: bool = False


def triage_candidates(candidates: list[dict[str, Any]], is_series: bool) -> ImportAttempt:
    attempt = ImportAttempt()
    largest = max((_size(candidate) for candidate in candidates), default=0)
    for candidate in candidates:
        blocking, overridden = _rejections(candidate, largest)
        if blocking:
            attempt.rejections.extend(blocking)
            continue
        entry = _import_file(candidate, is_series)
        if entry is None:
            attempt.rejections.append("fichier non rattaché à un média par Sonarr/Radarr")
            continue
        attempt.files.append(entry)
        attempt.overridden.extend(overridden)
    attempt.rejections = list(dict.fromkeys(attempt.rejections))
    attempt.overridden = list(dict.fromkeys(attempt.overridden))
    return attempt


async def prepare_import(
    client: ArrClient, download_id: str | None, is_series: bool, output_path: str | None, media_id: int | None
) -> ImportAttempt:
    """Fichiers d'un téléchargement prêts à importer. `download_id` et
    `output_path` viennent de la file d'attente de Sonarr/Radarr, jamais de
    l'utilisateur.

    Par téléchargement d'abord, puis par dossier de sortie (Sonarr/Radarr ne
    retrouve plus un `downloadId` qu'il ne suit plus, alors que le dossier
    existe toujours), puis, s'il ne propose toujours rien, relance de sa tâche
    « traiter les téléchargements » — le geste qu'on ferait dans son interface."""
    candidates: list[dict[str, Any]] = []
    if download_id:
        found = await client.manual_import_candidates(download_id=download_id, media_id=media_id)
        candidates = found if isinstance(found, list) else []
    if not candidates and output_path:
        found = await client.manual_import_candidates(folder=output_path)
        candidates = found if isinstance(found, list) else []
    if not candidates:
        await client.process_monitored_downloads()
        return ImportAttempt(relaunched=True)
    return triage_candidates(candidates, is_series)


async def wait_for_command(client: ArrClient, command_id: int | None) -> str | None:
    """État final de la commande, ou None si elle tourne encore au bout du
    délai (l'import continue alors chez Sonarr/Radarr)."""
    if command_id is None:
        return None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + COMMAND_TIMEOUT_SECONDS
    while True:
        command = await client.get_command(command_id)
        status = str(command.get("status") or "").lower() if isinstance(command, dict) else ""
        if status in _FINISHED_COMMANDS:
            return status
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(COMMAND_POLL_SECONDS)


def _label(record: dict[str, Any], media: "Media") -> str:
    return str(record.get("title") or "") or media.title


def _attempt_step(label: str, attempt: ImportAttempt) -> DeleteStepResult | None:
    """Étape d'échec d'un téléchargement qui n'a rien à envoyer, sinon None."""
    if attempt.files:
        return None
    if attempt.relaunched:
        error = (
            "Sonarr/Radarr ne trouve aucun fichier pour ce téléchargement (dossier de sortie absent ou "
            "inaccessible depuis Sonarr/Radarr) ; traitement de sa file d'attente relancé."
        )
    else:
        reasons = " · ".join(attempt.rejections)
        error = f"Refusé par Sonarr/Radarr, à trancher dans son import manuel : {reasons}" if reasons else ""
    return DeleteStepResult(kind="import_retry", label=label, success=False, error=error[:_MAX_REASON] or None)


def _verified_step(
    label: str, attempt: ImportAttempt, command_status: str | None, still_blocked: dict[str, Any] | None
) -> DeleteStepResult:
    """Résultat constaté APRÈS l'import : la commande ne signale pas l'échec
    d'un fichier (ManualImportService.Execute), seule la file d'attente le dit."""
    count = len(attempt.files)
    overridden = f" (malgré : {' · '.join(attempt.overridden)})" if attempt.overridden else ""
    if command_status is None:
        return DeleteStepResult(
            kind="import_retry",
            label=f"{label} — import de {count} fichier(s) en cours chez Sonarr/Radarr{overridden}",
            success=True,
        )
    if command_status != "completed":
        return DeleteStepResult(
            kind="import_retry", label=label, success=False, error=f"Commande d'import interrompue ({command_status})."
        )
    if still_blocked is not None:
        reason = _reason(still_blocked) or "import toujours bloqué"
        return DeleteStepResult(
            kind="import_retry",
            label=label,
            success=False,
            error=f"Sonarr/Radarr n'a pas pu tout importer : {reason}"[:_MAX_REASON],
        )
    return DeleteStepResult(
        kind="import_retry", label=f"{label} — {count} fichier(s) importé(s){overridden}", success=True
    )


def _blocked_by_download(records: list[dict[str, Any]], key: str, arr_id: int) -> dict[str, dict[str, Any]]:
    return {
        str(record.get("downloadId") or record.get("id")): record
        for record in index_queue_issues(records, key).get(arr_id, [])
        if _kind(record) == IMPORT_KIND
    }


async def _prepare_all(
    client: ArrClient, blocked: dict[str, dict[str, Any]], media: "Media", is_series: bool, arr_id: int
) -> tuple[list[DeleteStepResult], dict[str, ImportAttempt]]:
    """Fichiers à importer par téléchargement bloqué, et étapes d'échec de
    ceux qui n'ont rien à envoyer."""
    steps: list[DeleteStepResult] = []
    sent: dict[str, ImportAttempt] = {}
    for download, record in blocked.items():
        download_id = str(record.get("downloadId") or "") or None
        output_path = str(record.get("outputPath") or "") or None
        if not download_id and not output_path:
            steps.append(
                DeleteStepResult(
                    kind="import_retry",
                    label=_label(record, media),
                    success=False,
                    error="Téléchargement inconnu de Sonarr/Radarr : import à relancer depuis leur interface.",
                )
            )
            continue
        attempt = await prepare_import(client, download_id, is_series, output_path, None if is_series else arr_id)
        failure = _attempt_step(_label(record, media), attempt)
        if failure is not None:
            steps.append(failure)
        else:
            sent[download] = attempt
    return steps, sent


async def _retry_live_imports(
    client: ArrClient, media: "Media", is_series: bool, arr_id: int
) -> tuple[list[DeleteStepResult], int]:
    key = "seriesId" if is_series else "movieId"
    blocked = _blocked_by_download(await client.get_media_queue(arr_id), key, arr_id)
    if not blocked:
        # Réglé entre-temps (import fait, téléchargement retiré) : la fiche
        # n'attendait plus que la prochaine analyse.
        label = f"{media.title} — plus aucun import bloqué"
        return [DeleteStepResult(kind="import_retry", label=label, success=True)], 0

    steps, sent = await _prepare_all(client, blocked, media, is_series, arr_id)
    if not sent:
        return steps, 0

    # Une seule commande pour tout le média, comme l'import manuel de
    # Sonarr/Radarr, puis une seule relecture de la file pour constater.
    files = [file for attempt in sent.values() for file in attempt.files]
    status = await wait_for_command(client, await client.manual_import(files))
    after = _blocked_by_download(await client.get_media_queue(arr_id), key, arr_id) if status == "completed" else {}
    for download, attempt in sent.items():
        steps.append(_verified_step(_label(blocked[download], media), attempt, status, after.get(download)))
    if status is None:
        return steps, len(files)
    if status != "completed":
        return steps, 0
    return steps, sum(len(attempt.files) for download, attempt in sent.items() if download not in after)


async def execute_import_retry(session: "Session", media: "Media", settings: "Settings") -> tuple[list[Any], int]:
    """Relance l'import de tous les téléchargements bloqués d'un média.
    Renvoie (étapes, nombre de fichiers importés ou en cours d'import).

    Seul l'identifiant du média vient de l'utilisateur. Le dernier scan dit
    s'il y a quelque chose à relancer ; la file d'attente est ensuite RELUE en
    direct (même rattachement strict par identifiant) : l'état relevé au scan
    peut avoir changé, et relancer un import déjà fait ne produirait que des
    erreurs. Après coup, une analyse du média est demandée au temps réel pour
    que la fiche reflète le résultat sans attendre le prochain scan."""
    has_issue = any(
        issue.kind == IMPORT_KIND
        for issue in session.exec(select(ImportIssue).where(ImportIssue.media_id == media.id)).all()
    )
    # Un téléchargement en souffrance n'a rien à importer : il n'est pas
    # encore arrivé. Seuls les imports bloqués se relancent.
    if not has_issue:
        return [], 0

    is_series = media.media_type == MediaType.series
    target = arr_target_for(session, settings, media)
    arr_id = media.sonarr_id if is_series else media.radarr_id
    if target is None or not arr_id:
        missing = DeleteStepResult(kind="import_retry", label=media.title, success=False, error="Instance introuvable.")
        return [missing], 0
    client: ArrClient = target.sonarr() if is_series else target.radarr()

    try:
        steps, imported = await _retry_live_imports(client, media, is_series, arr_id)
    except httpx.HTTPError as exc:
        return [DeleteStepResult(kind="import_retry", label=media.title, success=False, error=http_error_text(exc))], 0
    if media.id is not None and any(step.success for step in steps):
        hub.media_changed(media.id)
    return steps, imported
