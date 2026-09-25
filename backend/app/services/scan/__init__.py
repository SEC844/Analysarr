"""Moteur de scan : lit Sonarr, Radarr, le serveur multimédia et le client
torrent, puis calcule l'état de santé de chaque média.

Organisation :
- statuses.py : liste fermée des statuts et leur calcul ;
- library.py : fichiers de la bibliothèque et rapprochement avec Sonarr/Radarr ;
- results.py : construction d'un média (partagée avec les analyses par service) ;
- collect.py : lecture de tous les services pour un scan complet ;
- orchestrator.py : déroulé d'une analyse, verrou, enregistrement, notifications.

Ce module réexporte l'API utilisée ailleurs : les imports
`from app.services.scan import ...` n'ont pas à connaître ce découpage."""

import asyncio

from app.services.scan.collect import _collect
from app.services.scan.library import (
    _current_flags,
    _episode_label,
    _episode_span_labels,
    _file_match_strength,
    _index_items,
    _media_sources,
    _missing_emby_labels,
    _pick_emby_item,
    _pick_series_item,
    _without_other_instance_files,
)
from app.services.scan.orchestrator import _fail_scan, _run_automations, _run_scan_impl
from app.services.scan.results import (
    LibraryContext,
    MediaBuildResult,
    build_movie_result,
    build_series_result,
    build_untracked_movie,
    build_untracked_results,
    build_untracked_series,
)
from app.services.scan.statuses import (
    DETECTION_EVENTS,
    INFO_STATUSES,
    MEDIA_STATUSES,
    alert_statuses,
    compute_statuses,
    current_files_size,
    is_healthy,
    is_tracked_by_arr,
)
from app.services.scan_scopes import SERVICE_SCOPES

_scan_lock = asyncio.Lock()


def is_scan_running() -> bool:
    return _scan_lock.locked()


async def run_scan(trigger: str = "manual", scope: str = "full") -> None:
    """Un seul verrou pour TOUTES les analyses : une analyse partielle et un
    scan complet ne peuvent jamais écrire en même temps dans le cache."""
    if _scan_lock.locked():
        return
    async with _scan_lock:
        if scope in SERVICE_SCOPES:
            # import local : évite le cycle scan ↔ partial_scan (qui importe ce package)
            from app.services.partial_scan import run_service_scan

            await run_service_scan(scope, trigger)
            return
        await _run_scan_impl(trigger, scope)


# Références fortes vers les analyses lancées en tâche de fond : asyncio ne
# garde qu'une référence faible, une analyse pourrait sinon être collectée en
# cours de route.
_background_scans: set[asyncio.Task] = set()


def launch_scan(scope: str = "full", trigger: str = "manual") -> bool:
    """Lance une analyse en tâche de fond. Renvoie False si une analyse tourne
    déjà (le verrou est unique, voir `run_scan`)."""
    if is_scan_running():
        return False
    task = asyncio.create_task(run_scan(trigger=trigger, scope=scope))
    _background_scans.add(task)
    task.add_done_callback(_background_scans.discard)
    return True


__all__ = [
    "DETECTION_EVENTS",
    "INFO_STATUSES",
    "MEDIA_STATUSES",
    "LibraryContext",
    "MediaBuildResult",
    "_collect",
    "_current_flags",
    "_episode_label",
    "_episode_span_labels",
    "_fail_scan",
    "_file_match_strength",
    "_index_items",
    "_media_sources",
    "_missing_emby_labels",
    "_pick_emby_item",
    "_pick_series_item",
    "_run_automations",
    "_without_other_instance_files",
    "alert_statuses",
    "build_movie_result",
    "build_series_result",
    "build_untracked_movie",
    "build_untracked_results",
    "build_untracked_series",
    "compute_statuses",
    "current_files_size",
    "is_healthy",
    "is_scan_running",
    "is_tracked_by_arr",
    "launch_scan",
    "run_scan",
]
