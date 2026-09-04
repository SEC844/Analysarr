import os
import re

import httpx

from app.models.media import MediaFile
from app.models.settings import Settings
from app.schemas.media import CrossSeedSearchResult

_SEASON_PATTERN = re.compile(r"^S(\d+)", re.IGNORECASE)


def _translate_path_for_cross_seed(path: str, settings: Settings) -> str:
    """Traduit un chemin vu depuis Analysarr/Emby (`emby_library_path`) vers
    le même chemin vu depuis le conteneur cross-seed (`cross_seed_library_path`)
    — les deux conteneurs peuvent monter le même volume à un endroit
    différent. Sans traduction, cross-seed reçoit un chemin qu'il ne peut pas
    résoudre sur son propre système de fichiers et rejette la requête (HTTP
    400 "A valid infoHash or an accessible path must be provided"), même si
    le chemin est parfaitement valide côté Analysarr."""
    if not settings.emby_library_path or not settings.cross_seed_library_path:
        return path
    from_prefix = settings.emby_library_path.rstrip("/\\")
    to_prefix = settings.cross_seed_library_path.rstrip("/\\")
    if path == from_prefix:
        return to_prefix
    for sep in ("/", "\\"):
        if path.startswith(from_prefix + sep):
            return to_prefix + sep + path[len(from_prefix) + 1 :]
    return path


def _season_number(episode_label: str | None) -> int | None:
    if not episode_label:
        return None
    match = _SEASON_PATTERN.match(episode_label)
    return int(match.group(1)) if match else None


def _common_dir(paths: list[str]) -> str:
    if len(paths) == 1:
        return os.path.dirname(paths[0])
    return os.path.commonpath(paths)


def paths_for_scope(files: list[MediaFile], scope: str) -> list[str]:
    """Chemins à envoyer à cross-seed selon la granularité demandée. Le
    webhook `path` accepte aussi bien un fichier qu'un dossier — cross-seed
    explore alors récursivement le dossier donné (`findPotentialNestedRoots`,
    voir la doc/le code source) — c'est ce qui permet de choisir la portée de
    la recherche simplement en changeant le chemin envoyé, sans paramètre
    dédié côté cross-seed :
    - "episode" : un chemin par fichier d'épisode (comportement historique).
    - "season" : un chemin par dossier de saison (parent commun aux épisodes
      de cette saison) — une seule requête cross-seed par saison.
    - "series" : un seul chemin, dossier parent commun à TOUS les épisodes de
      la série — une seule requête pour toute la série d'un coup."""
    paths = [f.path for f in files if f.path]
    if not paths:
        return []

    if scope == "series":
        return [_common_dir(paths)]

    if scope == "season":
        by_season: dict[int | None, list[str]] = {}
        for f in files:
            if f.path:
                by_season.setdefault(_season_number(f.episode_label), []).append(f.path)
        return [
            _common_dir(season_paths)
            for _season, season_paths in sorted(by_season.items(), key=lambda kv: (kv[0] is None, kv[0]))
        ]

    return paths  # "episode" (ou valeur inconnue) : repli sur le comportement historique


async def trigger_cross_seed_search(
    settings: Settings,
    torrent_hashes: list[str],
    files: list[MediaFile] | None = None,
    scope: str = "episode",
) -> CrossSeedSearchResult:
    """Déclenche une recherche cross-seed.

    Portée "episode" (par défaut, inchangé) : recherche par infoHash pour
    chaque torrent connu ; si le média n'a AUCUN torrent (statut
    manquant_qbit), repli sur le chemin de chaque fichier Emby.

    Portées "season"/"series" : recherche TOUJOURS par chemin, à la
    granularité demandée (voir `paths_for_scope`), même si des torrents
    existent déjà pour certains épisodes — c'est une recherche
    délibérément plus large qu'une simple recherche par torrent connu,
    utile pour retrouver un season pack ou un intégral sur un autre tracker.

    `ignoreExcludeRecentSearch=true` : sans ça, cross-seed ignore
    silencieusement toute requête pour un torrent/chemin déjà cherché
    récemment (équivalent HTTP du flag CLI `--ignore-timestamps`) — une
    recherche déclenchée manuellement depuis la fiche média doit toujours
    s'exécuter, pas être ignorée en silence."""
    if not (settings.cross_seed_enabled and settings.cross_seed_url and settings.cross_seed_api_key):
        return CrossSeedSearchResult(triggered=0, errors=["cross-seed n'est pas activé ou configuré."])

    base = settings.cross_seed_url.rstrip("/")
    errors: list[str] = []
    triggered = 0

    targets: list[tuple[str, dict[str, str]]]
    if scope == "episode" and torrent_hashes:
        targets = [(h, {"infoHash": h, "ignoreExcludeRecentSearch": "true"}) for h in torrent_hashes]
    else:
        paths = paths_for_scope(files or [], scope)
        targets = [
            (p, {"path": _translate_path_for_cross_seed(p, settings), "ignoreExcludeRecentSearch": "true"})
            for p in paths
        ]

    async with httpx.AsyncClient(timeout=15.0) as client:
        for label, body in targets:
            try:
                resp = await client.post(
                    f"{base}/api/webhook",
                    params={"apikey": settings.cross_seed_api_key},
                    data=body,
                )
                resp.raise_for_status()
                triggered += 1
            except httpx.HTTPStatusError as exc:
                # Le corps de la réponse de cross-seed explique précisément le refus
                # (ex : chemin hors de ses dataDirs configurés) — sans lui, l'erreur
                # httpx générique ("400 Bad Request") ne dit rien d'exploitable.
                detail = exc.response.text.strip()[:200] or exc.response.reason_phrase
                errors.append(f"{label} : HTTP {exc.response.status_code} — {detail}")
            except httpx.HTTPError as exc:
                errors.append(f"{label} : {exc}")

    return CrossSeedSearchResult(triggered=triggered, errors=errors)
