import errno
import logging
import os
from collections.abc import Sequence

from sqlmodel import Session, select

from app.clients.torrent import TorrentClient, torrent_client
from app.models.ids import row_id
from app.models.media import Media, MediaFile, MediaType, Torrent
from app.models.settings import Settings
from app.schemas.media import (
    HardlinkRepairItem,
    HardlinkRepairPreview,
    HardlinkRepairResult,
    HardlinkRepairStepResult,
)
from app.services.hardlink import (
    episode_label_from_filename,
    resolve_current_files,
    resolve_torrent_files,
    stat_inode,
)
from app.services.path_guard import ensure_paths_available, ensure_writable, replace_blockers
from app.services.scan.statuses import refresh_media_statuses

logger = logging.getLogger(__name__)


async def build_repair_preview(session: Session, media: Media, settings: Settings) -> HardlinkRepairPreview:
    """Pour chaque torrent orphelin (is_hardlinked=False) rattaché à ce média,
    tente d'apparier un de ses fichiers avec le fichier actuellement suivi par
    Emby/Sonarr/Radarr pour le même épisode (ou, pour un film, le fichier
    unique) — la paire que `execute_repair` remplacera par un hardlink.

    Deux sens de réparation sont possibles selon que la bibliothèque est déjà
    protégée ou non par un AUTRE torrent :
    - `torrent_to_library` (cas normal) : le fichier de la bibliothèque n'est
      protégé par aucun torrent — on le remplace par un hardlink vers le
      fichier de CE torrent.
    - `library_to_torrent` (bibliothèque déjà protégée ailleurs, ex :
      plusieurs copies cross-seed d'un même film déjà liées entre elles et à
      la bibliothèque) : remplacer le fichier de la bibliothèque romprait un
      hardlink fonctionnel pour le remplacer par un lien vers CE torrent —
      aucun gain de protection, juste un risque pour rien. Le fichier de LA
      BIBLIOTHÈQUE sert alors de source, et c'est le fichier de ce torrent
      (une copie séparée, non protégée) qui est remplacé par un hardlink vers
      elle — le torrent rejoint le même groupe de hardlinks sans qu'aucun
      lien existant ne soit jamais touché.

    Aucune vérification `st_dev` préalable pour décider d'écarter un
    candidat : sur certains montages virtualisés (ex : `shfs` d'Unraid, qui
    peut présenter un `st_dev` incohérent selon le chemin de montage utilisé
    pour atteindre un même fichier physique), deviner à l'avance si un
    hardlink va réussir n'est pas fiable — dans un sens comme dans l'autre.
    Chaque candidat au contenu vérifié est donc proposé, et c'est la
    tentative réelle dans `execute_repair` (via `_relink`) qui tranche :
    succès, ou échec EXDEV explicite si les deux fichiers sont vraiment sur
    des systèmes de fichiers différents. `_relink` ne modifie jamais rien
    tant que le nouveau lien n'a pas été créé avec succès — proposer un
    candidat qui échouera ne casse donc jamais rien, ça se contente de
    remonter l'erreur telle quelle."""
    all_files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    # Seuls les torrents "repairable" sont éligibles : leur contenu (même
    # épisode/média, même taille en octets) a déjà été vérifié identique à un
    # fichier actuellement suivi par la bibliothèque au moment du scan (voir
    # services/scan/statuses.py et torrent_match.py) — un vrai orphelin (contenu
    # différent, ex : ancienne qualité remplacée par un upgrade) n'est pas
    # réparable : le proposer ici remplacerait le bon fichier par le mauvais.
    orphan_torrents = session.exec(
        select(Torrent).where(
            Torrent.media_id == media.id,
            Torrent.is_hardlinked == False,  # noqa: E712
            Torrent.repairable == True,  # noqa: E712
        )
    ).all()
    protected_torrents = session.exec(
        select(Torrent).where(Torrent.media_id == media.id, Torrent.is_hardlinked == True)  # noqa: E712
    ).all()

    by_episode, current_single = resolve_current_files(list(all_files), media.media_type)
    if not orphan_torrents or (not by_episode and current_single is None):
        return HardlinkRepairPreview(
            items=[],
            unmatched_torrents=[t.name for t in orphan_torrents],
        )

    items: list[HardlinkRepairItem] = []
    async with torrent_client(settings) as qbit:
        protected_inodes = await _protected_inodes(qbit, protected_torrents)
        for t in orphan_torrents:
            torrent_files = await resolve_torrent_files(qbit, t)
            items += _repair_items(media, t, torrent_files, by_episode, current_single, protected_inodes)

    matched_torrent_ids = {item.torrent_id for item in items}
    unmatched = [t.name for t in orphan_torrents if t.id not in matched_torrent_ids]
    return HardlinkRepairPreview(
        items=items,
        unmatched_torrents=unmatched,
    )


async def _protected_inodes(qbit: TorrentClient, protected_torrents: Sequence[Torrent]) -> set[tuple[int, int]]:
    """Inodes de CHAQUE fichier de chaque torrent déjà protégé.

    `Torrent.inode`/`Torrent.device` (colonnes DB) ne retiennent QU'UN SEUL
    fichier représentatif par torrent (le premier résolu au scan) —
    insuffisant pour un torrent multi-fichiers (pack saison) : seul UN épisode
    serait alors reconnu comme protégé, les autres seraient à tort traités
    comme non protégés (direction `torrent_to_library` choisie par erreur, qui
    romprait un hardlink pourtant déjà fonctionnel pour ces épisodes-là)."""
    inodes: set[tuple[int, int]] = set()
    for t in protected_torrents:
        for path, _size in await resolve_torrent_files(qbit, t):
            inode = stat_inode(path)
            if inode is not None:
                inodes.add(inode)
    return inodes


def _repair_items(
    media: Media,
    torrent: Torrent,
    torrent_files: list[tuple[str, int | None]],
    by_episode: dict[str, MediaFile],
    current_single: MediaFile | None,
    protected_inodes: set[tuple[int, int]],
) -> list[HardlinkRepairItem]:
    """Paires (fichier de bibliothèque, fichier du torrent) à relier pour un
    torrent réparable."""
    items: list[HardlinkRepairItem] = []
    for path, size in torrent_files:
        if not os.path.isfile(path):
            continue
        if media.media_type == MediaType.series:
            label = episode_label_from_filename(os.path.basename(path))
            current = by_episode.get(label) if label else None
        else:
            current = current_single

        # Même vérification de contenu qu'au scan (même taille en octets) :
        # une source différente entre-temps (torrent modifié, fichier
        # remplacé) ne doit pas produire une réparation erronée.
        if current is None or size is None or current.size != size:
            continue
        item = _repair_item(current, torrent, path, size, protected_inodes)
        if item is None:
            continue
        items.append(item)
        if media.media_type == MediaType.movie:
            break  # un seul fichier actuel pour un film, inutile de continuer
    return items


def _repair_item(
    current: MediaFile, torrent: Torrent, path: str, size: int, protected_inodes: set[tuple[int, int]]
) -> HardlinkRepairItem | None:
    current_inode = stat_inode(current.path)
    if current_inode is not None and current_inode == stat_inode(path):
        return None  # déjà hardlinké (sécurité, ne devrait pas arriver ici)

    if current_inode is not None and current_inode in protected_inodes:
        # La bibliothèque est déjà protégée par un autre torrent : on ne la
        # touche pas, on répare le fichier de CE torrent.
        direction, source_path, target_path, target_exists = "library_to_torrent", current.path, path, True
    else:
        direction, source_path, target_path = "torrent_to_library", path, current.path
        target_exists = current_inode is not None

    return HardlinkRepairItem(
        media_file_id=row_id(current),
        episode_label=current.episode_label,
        torrent_id=row_id(torrent),
        torrent_name=torrent.name,
        direction=direction,
        source_path=source_path,
        target_path=target_path,
        target_exists=target_exists,
        size=size,
    )


def _separate_copy_size(target_path: str, source_path: str) -> int:
    """Espace libéré en remplaçant `target_path` par un lien vers
    `source_path` : sa taille s'il s'agit d'une copie distincte sans autre
    lien (`st_nlink == 1`), sinon 0 (déjà lié, ou encore référencé ailleurs)."""
    try:
        target = os.stat(target_path)
        source = os.stat(source_path)
    except OSError:
        # Illisible : aucun espace annoncé plutôt qu'une estimation fausse.
        logger.debug("Taille illisible pour la réparation de %s", target_path, exc_info=True)
        return 0
    if (target.st_ino, target.st_dev) == (source.st_ino, source.st_dev) or target.st_nlink > 1:
        return 0
    return target.st_size


def _relink(target_path: str, source_path: str) -> bool:
    """Remplace `target_path` par un lien vers `source_path`, SANS jamais
    supprimer l'original avant d'être certain que le lien peut être créé : le
    nouveau lien est d'abord créé à côté (fichier temporaire), et seul un
    `os.replace()` — atomique — vient ensuite écraser l'original. Renvoie
    True si un lien SYMBOLIQUE a été utilisé en repli, False si un hardlink
    classique a suffi.

    Si `os.link()` échoue avec EXDEV (`target_path` et `source_path` sont
    RÉELLEMENT sur des systèmes de fichiers différents — un hardlink est par
    nature impossible entre deux systèmes de fichiers, ce n'est pas une
    limite contournable en changeant l'appel), on retente avec `os.symlink()`
    : un lien symbolique traverse les points de montage sans problème, et
    coûte le même espace disque nul qu'un hardlink puisqu'aucune copie n'est
    faite. `stat_inode()` (hardlink.py) utilise partout `os.stat()`, JAMAIS
    `os.lstat()` — un lien symbolique est donc vu exactement comme un
    hardlink par tout le reste de l'app (même `(inode, device)` que le
    fichier cible réel, remonté par la resolution du lien). Contrepartie : le
    conteneur qui lit ce chemin (Emby pour la bibliothèque, qBittorrent pour
    un torrent) doit pouvoir résoudre lui-même `source_path` — même
    prérequis de montages identiques déjà nécessaire pour qu'un hardlink
    fonctionne. Avant de substituer la cible, on vérifie que le lien créé
    résout bien vers un fichier réel DEPUIS CE CONTENEUR (`os.path.exists`,
    qui suit les liens) : sinon, rien n'est jamais substitué, la cible reste
    intacte et l'erreur d'origine est remontée telle quelle."""
    tmp_path = f"{target_path}.analysarr-tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    used_symlink = False
    try:
        os.link(source_path, tmp_path)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        used_symlink = True
        try:
            os.symlink(source_path, tmp_path)
            if not os.path.exists(tmp_path):
                os.remove(tmp_path)
                raise OSError(errno.ENOENT, "le lien symbolique créé ne résout vers aucun fichier")
        except OSError as fallback_exc:
            if os.path.lexists(tmp_path):
                os.remove(tmp_path)
            raise OSError(
                errno.EXDEV,
                "Hardlink impossible : le torrent et le fichier de la bibliothèque sont sur des systèmes de "
                "fichiers différents. Le repli par lien symbolique a aussi échoué "
                f"({fallback_exc}) — aucune modification effectuée.",
            ) from fallback_exc
    os.replace(tmp_path, target_path)
    return used_symlink


async def execute_repair(session: Session, media: Media, settings: Settings) -> HardlinkRepairResult:
    """Pour chaque paire identifiée par `build_repair_preview` : remplace
    `target_path` par un hardlink vers `source_path` (voir les deux sens
    possibles dans `build_repair_preview`). Voir `_relink` : l'original n'est
    jamais perdu si la création du lien échoue."""
    preview = await build_repair_preview(session, media, settings)
    # Voir services/path_guard.py : écrire sous un point de montage vide
    # cacherait le fichier créé dès que le volume est remonté.
    ensure_paths_available(
        settings,
        [path for item in preview.items for path in (item.target_path, item.source_path)],
        "Réparation",
    )
    # Le lien est créé à côté de la cible avant de la remplacer : sans droit
    # d'écriture dans ce dossier, la réparation serait refusée fichier par
    # fichier, et donc à moitié faite.
    ensure_writable([path for item in preview.items for path in replace_blockers(item.target_path)], "Réparation")

    steps: list[HardlinkRepairStepResult] = []
    freed_bytes = 0
    for item in preview.items:
        label = item.episode_label or item.target_path
        replaced_size = _separate_copy_size(item.target_path, item.source_path)
        try:
            used_symlink = _relink(item.target_path, item.source_path)
            freed_bytes += replaced_size
            steps.append(
                HardlinkRepairStepResult(
                    media_file_id=item.media_file_id, label=label, success=True, used_symlink=used_symlink
                )
            )
        except OSError as exc:
            steps.append(
                HardlinkRepairStepResult(media_file_id=item.media_file_id, label=label, success=False, error=str(exc))
            )

    # Recalcul immédiat : les fichiers réparés partagent maintenant le même
    # inode, donc plus orphelin_qbit/manquant_qbit pour eux — sans ce recalcul
    # la fiche resterait fausse jusqu'au prochain scan complet.

    repaired_torrent_ids: set[int] = set()
    repaired_media_file_ids: set[int] = set()
    repaired_torrent_target_path: dict[int, str] = {}
    for item, step in zip(preview.items, steps, strict=True):
        if not step.success:
            continue
        repaired_torrent_ids.add(item.torrent_id)
        if item.direction == "torrent_to_library":
            repaired_media_file_ids.add(item.media_file_id)
        else:
            repaired_torrent_target_path[item.torrent_id] = item.target_path

    all_files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    all_torrents = session.exec(select(Torrent).where(Torrent.media_id == media.id)).all()

    for f in all_files:
        if f.id in repaired_media_file_ids:
            # Direction torrent_to_library : le chemin de la bibliothèque a
            # changé d'inode (nouveau hardlink vers le torrent).
            inode = stat_inode(f.path)
            f.inode, f.device = (inode[0], inode[1]) if inode else (None, None)
            session.add(f)
    for t in all_torrents:
        if t.id in repaired_torrent_ids:
            target_path = repaired_torrent_target_path.get(t.id)
            if target_path is not None:
                # Direction library_to_torrent : le fichier de CE torrent a
                # changé d'inode (nouveau hardlink vers la bibliothèque, elle
                # inchangée) — on recalcule le sien pour refléter le nouveau
                # groupe de hardlinks.
                inode = stat_inode(target_path)
                t.inode, t.device = (inode[0], inode[1]) if inode else (None, None)
            t.is_hardlinked = True
            session.add(t)

    refresh_media_statuses(session, media)
    session.commit()

    return HardlinkRepairResult(steps=steps, freed_bytes=freed_bytes)
