"""Statuts d'un média : liste fermée, calcul à partir de ses fichiers et torrents."""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.models.media import Media, MediaFile, Torrent
from app.services.queue_issues import IMPORT_KIND, STALLED_KIND

# Liste fermée des statuts qu'un média peut porter : elle borne le filtre de
# l'API et sert de référence à l'interface (types/media.ts).
MEDIA_STATUSES = (
    "doublon",
    "orphelin_qbit",
    "non_hardlink",
    "tracker_unique",
    "cross_seed",
    "manquant_emby",
    "manquant_qbit",
    "manquant_arr",
    "import_rate",
    "telechargement_bloque",
    "non_importe",
)

# Statuts purement informatifs : couverture tracker, et saisons téléchargées
# mais pas encore importées. Ils n'empêchent jamais un média d'être sain
# (voir `is_healthy`).
INFO_STATUSES = frozenset({"tracker_unique", "cross_seed", "non_importe"})


def alert_statuses(statuses: set[str] | list[str]) -> set[str]:
    """Statuts qui demandent une action. Tout le reste est informatif."""
    return {status for status in statuses if status and status not in INFO_STATUSES}


def is_healthy(statuses: set[str] | list[str]) -> bool:
    """Média sain : suivi par Sonarr/Radarr, présent sur le serveur multimédia
    et protégé par un torrent hardlinké. Ces trois conditions sont exactement
    l'absence de `manquant_arr`, `manquant_emby` et `manquant_qbit`, auxquelles
    s'ajoute l'absence de tout autre problème (doublon, orphelin...)."""
    return not alert_statuses(statuses)


# Statuts dont l'APPARITION est notifiable (événement -> statut calculé au scan).
DETECTION_EVENTS = {
    "orphan_detected": "orphelin_qbit",
    "duplicate_detected": "doublon",
    "non_hardlink_detected": "non_hardlink",
    "import_failed_detected": "import_rate",
    "stalled_download_detected": "telechargement_bloque",
    "untracked_detected": "manquant_arr",
}


def current_files_size(files: list[MediaFile]) -> int:
    """Poids réellement occupé par le média : ses fichiers actuellement suivis
    (hors doublons), ou tous ses fichiers si aucun n'a pu être identifié."""
    current = [f for f in files if f.is_current] or files
    return sum(f.size or 0 for f in current)


def is_tracked_by_arr(media: Media) -> bool:
    """Média suivi par un Radarr/Sonarr. Faux pour un média trouvé dans la
    bibliothèque seule (voir `build_untracked_results`)."""
    return media.radarr_id is not None or media.sonarr_id is not None


# Statuts calculés sur l'état de hardlink des torrents : tant qu'un torrent
# n'a pas pu être évalué (chemins inaccessibles), leur situation est
# incertaine et une alerte masquée n'est ni confirmée ni réactivée.
HARDLINK_STATUSES = frozenset({"orphelin_qbit", "non_hardlink", "manquant_qbit"})


@dataclass
class StatusEvaluation:
    statuses: set[str]
    # Espace récupérable par statut (doublons, torrents orphelins) : masquer
    # l'alerte retire aussi son espace du total affiché.
    reclaimable: dict[str, int] = field(default_factory=dict)
    # Ce qui déclenche chaque alerte (fichiers, torrents, téléchargements en
    # cause). Une alerte masquée revient dès que cette situation change.
    situations: dict[str, str] = field(default_factory=dict)
    # Alertes dont la situation n'a pas pu être évaluée cette fois-ci.
    uncertain: set[str] = field(default_factory=set)


def compute_statuses(
    files: list[MediaFile],
    torrents: list[Torrent],
    has_emby_item: bool,
    missing_emby_episode_count: int = 0,
    import_blocked_hashes: set[str] | None = None,
    queue_kinds: set[str] | None = None,
    tracked_by_arr: bool = True,
) -> tuple[set[str], int]:
    """Statuts du média et espace récupérable (doublons + torrents orphelins)."""
    evaluation = evaluate_statuses(
        files,
        torrents,
        has_emby_item,
        missing_emby_episode_count,
        import_blocked_hashes,
        queue_kinds,
        tracked_by_arr,
    )
    return evaluation.statuses, sum(evaluation.reclaimable.values())


def evaluate_statuses(
    files: list[MediaFile],
    torrents: list[Torrent],
    has_emby_item: bool,
    missing_emby_episode_count: int = 0,
    import_blocked_hashes: set[str] | None = None,
    queue_kinds: set[str] | None = None,
    tracked_by_arr: bool = True,
) -> StatusEvaluation:
    """Statuts, espace récupérable par statut et situation de chaque alerte.

    Un fichier gardé volontairement ou un torrent ignoré (`ignored`, voir
    services/ignores.py) ne déclenche plus d'alerte le concernant — doublon,
    orphelin, non hardlinké. Un torrent réparable ignoré seede pourtant
    toujours le média : il compte encore contre « non seedé »."""
    # Un torrent dont Sonarr/Radarr attend encore l'import n'est ni un
    # orphelin ni une copie à réparer : son fichier n'a simplement pas encore
    # rejoint la bibliothèque. Le proposer au nettoyage supprimerait le
    # téléchargement que l'utilisateur essaie justement d'importer.
    blocked = import_blocked_hashes or set()
    torrents = [t for t in torrents if (t.hash or "").lower() not in blocked]
    kinds = queue_kinds or set()
    found = _Findings.of(files, torrents)

    present = {
        "doublon": bool(found.duplicates),
        "orphelin_qbit": bool(found.orphans),
        "non_hardlink": bool(found.repairables),
        "manquant_emby": _missing_from_media_server(files, has_emby_item, missing_emby_episode_count, kinds),
        # Présent dans la bibliothèque mais suivi par aucun Radarr/Sonarr : pas
        # de mise à jour de qualité, pas de suppression propre, pas de renommage.
        "manquant_arr": not tracked_by_arr,
        # Un torrent réparable : ce média EST bien seedé — juste pas protégé
        # par hardlink, même ignoré.
        "manquant_qbit": _missing_from_torrent_client(torrents, any(_is_repairable(t) for t in torrents), kinds),
        "non_importe": found.not_imported,
    }
    statuses = {status for status, is_present in present.items() if is_present} | _queue_statuses(kinds)
    coverage = _tracker_coverage(torrents)
    if coverage is not None:
        statuses.add(coverage)

    queue = ",".join(sorted(blocked | kinds))
    return StatusEvaluation(
        statuses=statuses,
        reclaimable={
            "doublon": _duplicate_bytes(found.active_files) or 0,
            "orphelin_qbit": _orphan_bytes(found.orphans) or 0,
        },
        situations={
            "doublon": _joined(f.path for f in found.duplicates),
            "orphelin_qbit": _joined(t.hash.lower() for t in found.orphans),
            "non_hardlink": _joined(t.hash.lower() for t in found.repairables),
            "manquant_emby": f"{has_emby_item}:{missing_emby_episode_count}",
            "import_rate": queue,
            "telechargement_bloque": queue,
        },
        uncertain=set(HARDLINK_STATUSES) if any(t.is_hardlinked is None for t in torrents) else set(),
    )


@dataclass
class _Findings:
    """Ce qui déclenche les alertes liées aux fichiers et aux torrents, hors
    éléments ignorés."""

    active_files: list[MediaFile]
    duplicates: list[MediaFile]
    orphans: list[Torrent]
    repairables: list[Torrent]
    not_imported: bool

    @classmethod
    def of(cls, files: list[MediaFile], torrents: list[Torrent]) -> "_Findings":
        active_files = [f for f in files if not f.ignored]
        active_torrents = [t for t in torrents if not t.ignored]
        return cls(
            active_files=active_files,
            duplicates=[f for group in duplicate_groups(active_files) for f in group],
            orphans=[t for t in active_torrents if is_orphan(t)],
            repairables=[t for t in active_torrents if _is_repairable(t)],
            not_imported=any(t.not_imported for t in active_torrents),
        )


def _joined(values: Iterable[str]) -> str:
    return "\n".join(sorted(values))


def duplicate_groups(files: Sequence[MediaFile]) -> list[list[MediaFile]]:
    """Fichiers en doublon, par épisode (ou pour le film) : plusieurs fichiers
    le portent et leurs inodes sont différents — ou ne peuvent pas être
    vérifiés. Des hardlinks d'un même fichier ne sont pas des doublons."""
    groups: dict[str | None, list[MediaFile]] = {}
    for f in files:
        groups.setdefault(f.episode_label, []).append(f)
    duplicates = []
    for group_files in groups.values():
        if len(group_files) <= 1:
            continue
        resolved = [(f.inode, f.device) for f in group_files if f.inode is not None]
        if not resolved or len(set(resolved)) > 1:
            duplicates.append(group_files)
    return duplicates


def _duplicate_bytes(files: list[MediaFile]) -> int | None:
    """Espace récupérable sur les doublons, ou None s'il n'y en a pas : tous
    les fichiers d'un groupe sauf le plus gros."""
    groups = duplicate_groups(files)
    if not groups:
        return None
    return sum(sum(sorted((f.size or 0 for f in group), reverse=True)[1:]) for group in groups)


def _orphan_bytes(torrents: list[Torrent]) -> int | None:
    """Espace récupérable sur les torrents orphelins, ou None s'il n'y en a pas.

    Un torrent "repairable" a le même contenu (même épisode/média, même taille
    en octets) qu'un fichier actuellement suivi, juste non hardlinké : ce n'est
    pas un orphelin à supprimer mais un candidat à la réparation de hardlink.
    Plusieurs orphelins peuvent être des copies cross-seed d'une même ancienne
    version (même inode entre eux) : supprimer l'un ne libère rien tant qu'un
    autre pointe vers ce fichier, chaque inode n'est donc compté qu'une fois.
    Un torrent « non importé » (saisons prises d'avance) n'est pas un orphelin."""
    orphans = [t for t in torrents if is_orphan(t)]
    if not orphans:
        return None
    total = 0
    seen_inodes: set[tuple[int, int | None]] = set()
    for t in orphans:
        if t.inode is not None:
            key = (t.inode, t.device)
            if key in seen_inodes:
                continue
            seen_inodes.add(key)
        total += t.size or 0
    return total


def _is_repairable(torrent: Torrent) -> bool:
    return torrent.is_hardlinked is False and torrent.repairable


def is_orphan(torrent: Torrent) -> bool:
    """Vrai orphelin : ni protégé, ni réparable, ni simplement pas encore
    importé. Seule définition utilisée par le nettoyage et les automatisations."""
    return torrent.is_hardlinked is False and not torrent.repairable and not torrent.not_imported


def _tracker_coverage(torrents: list[Torrent]) -> str | None:
    """Couverture tracker : information, pas problème de santé. Calculée sur
    les torrents qui protègent vraiment le média (hardlinkés) — les trackers
    d'un orphelin ne couvrent plus rien."""
    protecting = [t for t in torrents if t.is_hardlinked is True] or torrents
    domains = {d["domain"] for t in protecting for d in json.loads(t.trackers_json)}
    if len(domains) == 1:
        return "tracker_unique"
    if len(domains) > 1:
        return "cross_seed"
    return None


def _queue_statuses(kinds: set[str]) -> set[str]:
    """Problèmes de file d'attente Sonarr/Radarr. Un téléchargement en
    souffrance est purement informatif : Analysarr ne le supprime ni ne le
    relance, il le signale."""
    statuses: set[str] = set()
    if IMPORT_KIND in kinds:
        statuses.add("import_rate")
    if STALLED_KIND in kinds:
        statuses.add("telechargement_bloque")
    return statuses


def _missing_from_media_server(
    files: list[MediaFile], has_emby_item: bool, missing_episode_count: int, kinds: set[str]
) -> bool:
    """Absent du serveur multimédia, ou (séries) certains épisodes téléchargés
    par Sonarr n'y ont jamais été importés — sans que la série elle-même en
    soit absente.

    Un média sans aucun fichier dont l'import est bloqué n'est pas « absent du
    serveur multimédia » : il est bloqué en amont, et c'est ce que dit le statut
    import_rate. Afficher les deux enverrait l'utilisateur chercher un problème
    côté serveur multimédia."""
    explained_by_import = bool(kinds) and not files
    return (not has_emby_item or missing_episode_count > 0) and not explained_by_import


def _missing_from_torrent_client(torrents: list[Torrent], has_repairable: bool, kinds: set[str]) -> bool:
    """Aucun torrent actif confirmé ni réparable (donc bien seedé) : soit aucun
    torrent du tout, soit tous orphelins. Si le hardlink n'a pas pu être évalué
    (chemins non montés), on ne se prononce pas plutôt que de produire des faux
    positifs en masse. Un média en cours de téléchargement, ou bloqué à
    l'import, n'est pas « non seedé » : son contenu est en route."""
    has_active_torrent = any(t.is_hardlinked is True for t in torrents)
    has_unresolved_torrent = any(t.is_hardlinked is None for t in torrents)
    return not (has_active_torrent or has_unresolved_torrent or has_repairable or kinds)
