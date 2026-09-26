"""Écriture des statuts d'un média : seul point qui écrit `statuses`,
`muted_statuses`, `reclaimable_bytes` et `total_size`.

Le scan complet, les analyses par service, l'analyse d'un média et les
actions (nettoyage, réparation, suppression) passent tous par ici : ils
calculent donc exactement la même chose, à partir de TOUTES les sources —
fichiers, torrents, file d'attente, épisodes absents du serveur multimédia,
suivi Sonarr/Radarr et éléments ignorés. Bug réel : une action recalculait
sans la file d'attente, les épisodes absents ni le suivi, et un média non
suivi perdait son alerte jusqu'au scan suivant."""

from collections.abc import Sequence

from sqlmodel import Session, col, select

from app.models.media import ImportIssue, Media, MediaFile, Torrent
from app.services.ignores import IgnoreSet, media_key
from app.services.scan.statuses import current_files_size, evaluate_statuses, is_tracked_by_arr


def apply_statuses(
    media: Media,
    files: Sequence[MediaFile],
    torrents: Sequence[Torrent],
    issues: Sequence[ImportIssue],
    ignores: IgnoreSet,
) -> None:
    """Statuts du média, en mémoire. Les éléments ignorés sont marqués avant
    le calcul, les alertes masquées retirées après ; l'espace récupérable
    d'une alerte masquée ne compte plus. L'appelant enregistre `ignores`."""
    key = media_key(media)
    ignores.mark(key, files, torrents)
    evaluation = evaluate_statuses(
        list(files),
        list(torrents),
        bool(media.emby_item_id),
        len([label for label in media.missing_emby_episodes.split(",") if label]),
        {i.download_id.lower() for i in issues if i.download_id},
        {i.kind for i in issues},
        tracked_by_arr=is_tracked_by_arr(media),
    )
    muted = ignores.mute(key, evaluation)
    media.statuses = ",".join(sorted(evaluation.statuses - muted))
    media.muted_statuses = ",".join(sorted(muted))
    media.reclaimable_bytes = sum(size for status, size in evaluation.reclaimable.items() if status not in muted)
    media.total_size = current_files_size(list(files))


def refresh_statuses(session: Session, medias: Sequence[Media]) -> None:
    """`apply_statuses` sur ce que la base contient MAINTENANT pour ces médias
    (après une action, ou une analyse qui n'a relu qu'une source). Enregistre
    les règles d'ignore réactivées et committe."""
    ignores = IgnoreSet.load(session)
    for media in medias:
        files = session.exec(select(MediaFile).where(col(MediaFile.media_id) == media.id)).all()
        torrents = session.exec(select(Torrent).where(col(Torrent.media_id) == media.id)).all()
        issues = session.exec(select(ImportIssue).where(col(ImportIssue.media_id) == media.id)).all()
        apply_statuses(media, files, torrents, issues, ignores)
        session.add(media)
        session.add_all([*files, *torrents])
    ignores.persist(session)


def refresh_media_statuses(session: Session, media: Media) -> None:
    refresh_statuses(session, [media])
