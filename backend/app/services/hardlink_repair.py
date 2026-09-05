import errno
import os

from sqlmodel import Session, select

from app.clients.qbittorrent import QbittorrentClient
from app.models.media import Media, MediaFile, MediaType, Torrent
from app.models.settings import Settings
from app.schemas.media import (
    HardlinkRepairItem,
    HardlinkRepairPreview,
    HardlinkRepairResult,
    HardlinkRepairStepResult,
)
from app.services.hardlink import episode_label_from_filename, resolve_current_files, stat_inode


async def _resolve_torrent_files(qbit: QbittorrentClient, torrent: Torrent) -> list[tuple[str, int | None]]:
    """[(chemin_absolu, taille)] pour chaque fichier réel du torrent — même
    logique que le scan (torrents/files + save_path), avec repli sur
    content_path si l'API n'a rien renvoyé."""
    try:
        files = await qbit.get_files(torrent.hash)
    except Exception:  # noqa: BLE001 - un échec ne doit pas bloquer le repli
        files = []
    resolved: list[tuple[str, int | None]] = []
    if files and torrent.save_path:
        for f in files:
            rel = f.get("name")
            if rel:
                resolved.append((os.path.join(torrent.save_path, rel), f.get("size")))
    elif torrent.content_path:
        resolved.append((torrent.content_path, torrent.size))
    return resolved


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

    Un seul cas reste écarté des réparations proposées (mais toujours
    reporté, pour transparence) : systèmes de fichiers différents. Le contenu
    correspond bien, mais le fichier du torrent et celui de la bibliothèque
    sont sur des disques/montages distincts (`st_dev` différent). `os.link()`
    échoue toujours avec EXDEV dans ce cas, quel que soit le sens du lien —
    ce n'est pas un choix de code à inverser, c'est une limite du système de
    fichiers. Seul un changement d'infrastructure (monter le dossier de
    téléchargement sur le même disque que la bibliothèque) peut le résoudre ;
    inutile de tenter et d'échouer à chaque fois."""
    all_files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    # Seuls les torrents "repairable" sont éligibles : leur contenu (même
    # épisode/média, même taille en octets) a déjà été vérifié identique à un
    # fichier actuellement suivi par la bibliothèque au moment du scan (voir
    # compute_statuses/_collect dans scan.py) — un vrai orphelin (contenu
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
    protected_inodes = {(t.inode, t.device) for t in protected_torrents if t.inode is not None and t.device is not None}

    by_episode, current_single = resolve_current_files(list(all_files), media.media_type)
    if not orphan_torrents or (not by_episode and current_single is None):
        return HardlinkRepairPreview(
            items=[],
            unmatched_torrents=[t.name for t in orphan_torrents],
            cross_filesystem_torrents=[],
        )

    items: list[HardlinkRepairItem] = []
    matched_torrent_ids: set[int] = set()
    cross_filesystem: list[str] = []

    async with QbittorrentClient(settings.qbittorrent_url, settings.qbittorrent_username, settings.qbittorrent_password) as qbit:
        for t in orphan_torrents:
            torrent_files = await _resolve_torrent_files(qbit, t)
            existing = [(p, size) for p, size in torrent_files if os.path.isfile(p)]
            if not existing:
                continue

            for path, size in existing:
                if media.media_type == MediaType.series:
                    label = episode_label_from_filename(os.path.basename(path))
                    current = by_episode.get(label) if label else None
                else:
                    current = current_single

                # Même vérification de contenu qu'au scan (même taille en
                # octets) : une source différente entre-temps (torrent modifié,
                # fichier remplacé) ne doit pas produire une réparation erronée.
                if current is None or size is None or current.size != size:
                    continue

                current_inode = stat_inode(current.path)
                torrent_inode = stat_inode(path)

                if current_inode is not None and current_inode == torrent_inode:
                    continue  # déjà hardlinké (sécurité, ne devrait pas arriver ici)

                # EXDEV est symétrique : peu importe le sens du lien envisagé,
                # deux systèmes de fichiers différents ne peuvent jamais être
                # hardlinkés entre eux. Vérifié avant de choisir un sens.
                if current_inode is not None and torrent_inode is not None and current_inode[1] != torrent_inode[1]:
                    if t.name not in cross_filesystem:
                        cross_filesystem.append(t.name)
                    continue

                already_protected = current_inode is not None and current_inode in protected_inodes
                if already_protected:
                    # La bibliothèque est déjà protégée par un autre torrent :
                    # on ne la touche pas, on répare le fichier de CE torrent.
                    direction = "library_to_torrent"
                    source_path, target_path = current.path, path
                    target_exists = True
                else:
                    direction = "torrent_to_library"
                    source_path, target_path = path, current.path
                    target_exists = current_inode is not None

                items.append(
                    HardlinkRepairItem(
                        media_file_id=current.id,
                        episode_label=current.episode_label,
                        torrent_id=t.id,
                        torrent_name=t.name,
                        direction=direction,
                        source_path=source_path,
                        target_path=target_path,
                        target_exists=target_exists,
                        size=size,
                    )
                )
                matched_torrent_ids.add(t.id)
                if media.media_type == MediaType.movie:
                    break  # un seul fichier actuel pour un film, inutile de continuer

    unmatched = [t.name for t in orphan_torrents if t.id not in matched_torrent_ids and t.name not in cross_filesystem]
    return HardlinkRepairPreview(
        items=items,
        unmatched_torrents=unmatched,
        cross_filesystem_torrents=cross_filesystem,
    )


def _relink(target_path: str, source_path: str) -> None:
    """Remplace `target_path` par un hardlink vers `source_path`, SANS jamais
    supprimer l'original avant d'être certain que le lien peut être créé : le
    nouveau lien est d'abord créé à côté (fichier temporaire), et seul un
    `os.replace()` — atomique — vient ensuite écraser l'original. Si
    `os.link()` échoue (ex: ERRNO 18 EXDEV — `target_path` et `source_path`
    sont sur des systèmes de fichiers différents, ce que `os.stat().st_dev`
    ne garantit pas toujours de détecter à l'avance), `target_path` n'a alors
    subi AUCUNE modification. Direction-agnostique : selon
    `HardlinkRepairItem.direction`, `target_path` peut être le fichier de la
    bibliothèque (cas normal) ou le fichier d'un torrent déjà protégé
    ailleurs (cas `library_to_torrent`)."""
    tmp_path = f"{target_path}.analysarr-tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    try:
        os.link(source_path, tmp_path)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            raise OSError(
                errno.EXDEV,
                "Hardlink impossible : le torrent et le fichier de la bibliothèque sont sur des systèmes de "
                "fichiers différents (le montage du dossier de téléchargement ne couvre pas le même disque que "
                "celui de la bibliothèque) — aucune modification effectuée.",
            ) from exc
        raise
    os.replace(tmp_path, target_path)


async def execute_repair(session: Session, media: Media, settings: Settings) -> HardlinkRepairResult:
    """Pour chaque paire identifiée par `build_repair_preview` : remplace
    `target_path` par un hardlink vers `source_path` (voir les deux sens
    possibles dans `build_repair_preview`). Voir `_relink` : l'original n'est
    jamais perdu si la création du lien échoue."""
    preview = await build_repair_preview(session, media, settings)

    steps: list[HardlinkRepairStepResult] = []
    for item in preview.items:
        label = item.episode_label or item.target_path
        try:
            _relink(item.target_path, item.source_path)
            steps.append(HardlinkRepairStepResult(media_file_id=item.media_file_id, label=label, success=True))
        except OSError as exc:
            steps.append(
                HardlinkRepairStepResult(media_file_id=item.media_file_id, label=label, success=False, error=str(exc))
            )

    # Recalcul immédiat : les fichiers réparés partagent maintenant le même
    # inode, donc plus orphelin_qbit/manquant_qbit pour eux — sans ce recalcul
    # la fiche resterait fausse jusqu'au prochain scan complet.
    from app.services.scan import compute_statuses  # import différé : évite un cycle avec scan.py

    repaired_torrent_ids: set[int] = set()
    repaired_media_file_ids: set[int] = set()
    repaired_torrent_target_path: dict[int, str] = {}
    for item, step in zip(preview.items, steps):
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

    statuses, reclaimable = compute_statuses(list(all_files), list(all_torrents), bool(media.emby_item_id))
    media.statuses = ",".join(sorted(statuses))
    media.reclaimable_bytes = reclaimable
    session.add(media)
    session.commit()

    return HardlinkRepairResult(steps=steps)
