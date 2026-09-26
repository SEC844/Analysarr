"""Création, retrait et liste des éléments ignorés (voir services/ignores.py
pour leur effet et leur réactivation).

Tout ce qui est ignoré est désigné par des identifiants du média — torrent,
fichier ou alerte qu'il porte ACTUELLEMENT — et revérifié ici : un client ne
peut jamais faire ignorer un hash, un chemin ou un statut arbitraire."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.models.ids import row_id
from app.models.ignore import IgnoreRule
from app.models.media import Media, MediaFile, Torrent
from app.schemas.ignores import IgnoreCreate, IgnoreRuleRead, MutedStatusRead
from app.services.action_log import MediaRef, record_action
from app.services.ignores import (
    IGNORABLE_TORRENT_STATES,
    MUTABLE_STATUSES,
    duplicate_file_ids,
    media_key,
    torrent_state,
)
from app.services.media_status import refresh_media_statuses

# Libellés de l'historique (textes du backend : en français, comme les
# détails d'erreur des services).
STATUS_NAMES = {
    "doublon": "Doublon",
    "orphelin_qbit": "Orphelin",
    "non_hardlink": "Non hardlinké",
    "manquant_emby": "Absent du serveur multimédia",
    "manquant_qbit": "Non seedé",
    "manquant_arr": "Non suivi",
    "import_rate": "Import raté",
    "telechargement_bloque": "Téléchargement en souffrance",
}


class IgnoreError(ValueError):
    """Demande refusée : la cible n'appartient pas au média, ne déclenche
    aucune alerte à ignorer, ou l'est déjà."""


@dataclass
class _Step:
    label: str
    success: bool = True
    error: str | None = None


@dataclass
class _Target:
    kind: str
    target: str
    label: str


def create_ignores(session: Session, media: Media, payload: IgnoreCreate) -> list[IgnoreRule]:
    """Crée les règles demandées, recalcule le média et trace l'action. La
    situation de chaque règle est relevée par ce recalcul même."""
    if payload.kind == "torrent":
        targets = [_torrent_target(session, media, payload.torrent_id)]
    elif payload.kind == "file":
        targets = [_file_target(session, media, payload.file_id)]
    else:
        targets = _status_targets(media, payload.statuses)
    key = media_key(media)
    for target in targets:
        if _existing(session, key, target) is not None:
            raise IgnoreError("Cet élément est déjà ignoré.")
    rules = [
        IgnoreRule(
            kind=target.kind,
            target=target.target,
            label=target.label,
            media_key=key,
            media_title=media.title,
            media_type=media.media_type.value,
            note=payload.note,
        )
        for target in targets
    ]
    session.add_all(rules)
    session.commit()
    refresh_media_statuses(session, media)
    record_action(session, "ignore", MediaRef.of(media), [_Step(_describe(rule)) for rule in rules])
    return rules


def delete_ignore(session: Session, rule: IgnoreRule) -> None:
    """Retire une règle : l'élément redevient signalé dès maintenant."""
    media = _media_of(session, rule, _media_by_key(session))
    ref = MediaRef.of(media) if media else MediaRef(id=None, title=rule.media_title, media_type=rule.media_type)
    label = _describe(rule)
    session.delete(rule)
    session.commit()
    if media is not None:
        refresh_media_statuses(session, media)
    record_action(session, "unignore", ref, [_Step(label)])


def list_ignores(session: Session) -> list[IgnoreRuleRead]:
    by_key = _media_by_key(session)
    reads = []
    for rule in session.exec(select(IgnoreRule).order_by(col(IgnoreRule.created_at).desc())).all():
        media = _media_of(session, rule, by_key)
        reads.append(
            IgnoreRuleRead(
                id=row_id(rule),
                kind="torrent" if rule.kind == "torrent" else "file" if rule.kind == "file" else "status",
                label=rule.label,
                status=rule.target if rule.kind == "status" else None,
                media_title=media.title if media else rule.media_title,
                media_type=rule.media_type,
                media_id=media.id if media else None,
                present=media is not None,
                note=rule.note,
                created_at=rule.created_at,
            )
        )
    return reads


@dataclass
class MediaIgnores:
    """Règles d'un média, pour sa fiche."""

    by_torrent_hash: dict[str, int]
    by_file_path: dict[str, int]
    muted: list[MutedStatusRead]


def media_ignores(
    session: Session, media: Media, files: Sequence[MediaFile], torrents: Sequence[Torrent]
) -> MediaIgnores:
    hashes = [t.hash.lower() for t in torrents]
    paths = [f.path for f in files]
    rules = session.exec(
        select(IgnoreRule).where(
            ((col(IgnoreRule.kind) == "status") & (col(IgnoreRule.media_key) == media_key(media)))
            | ((col(IgnoreRule.kind) == "torrent") & col(IgnoreRule.target).in_(hashes))
            | ((col(IgnoreRule.kind) == "file") & col(IgnoreRule.target).in_(paths))
        )
    ).all()
    muted_statuses = set(media.muted_statuses.split(","))
    return MediaIgnores(
        by_torrent_hash={r.target: row_id(r) for r in rules if r.kind == "torrent"},
        by_file_path={r.target: row_id(r) for r in rules if r.kind == "file"},
        muted=[
            MutedStatusRead(status=r.target, rule_id=row_id(r), note=r.note)
            for r in rules
            if r.kind == "status" and r.target in muted_statuses
        ],
    )


def torrent_is_ignorable(torrent: Torrent) -> bool:
    return not torrent.ignored and torrent_state(torrent) in IGNORABLE_TORRENT_STATES


# --- Validation -------------------------------------------------------------


def _torrent_target(session: Session, media: Media, torrent_id: int | None) -> _Target:
    torrent = session.get(Torrent, torrent_id) if torrent_id is not None else None
    if torrent is None or torrent.media_id != media.id:
        raise IgnoreError("Torrent introuvable pour ce média.")
    if not torrent_is_ignorable(torrent):
        raise IgnoreError("Ce torrent n'est signalé ni orphelin ni non hardlinké : rien à ignorer.")
    return _Target("torrent", torrent.hash.lower(), torrent.name)


def _file_target(session: Session, media: Media, file_id: int | None) -> _Target:
    file = session.get(MediaFile, file_id) if file_id is not None else None
    if file is None or file.media_id != media.id:
        raise IgnoreError("Fichier introuvable pour ce média.")
    files = session.exec(select(MediaFile).where(col(MediaFile.media_id) == media.id)).all()
    if row_id(file) not in duplicate_file_ids(files):
        raise IgnoreError("Ce fichier ne fait partie d'aucun doublon : rien à garder volontairement.")
    return _Target("file", file.path, file.path)


def _status_targets(media: Media, statuses: list[str]) -> list[_Target]:
    if not statuses or any(status not in MUTABLE_STATUSES for status in statuses):
        raise IgnoreError("Alerte inconnue.")
    current = set(media.statuses.split(","))
    if any(status not in current for status in statuses):
        raise IgnoreError("Ce média ne porte pas cette alerte.")
    return [_Target("status", status, STATUS_NAMES[status]) for status in dict.fromkeys(statuses)]


def _existing(session: Session, key: str, target: _Target) -> IgnoreRule | None:
    query = select(IgnoreRule).where(col(IgnoreRule.kind) == target.kind, col(IgnoreRule.target) == target.target)
    if target.kind == "status":
        query = query.where(col(IgnoreRule.media_key) == key)
    return session.exec(query).first()


def _describe(rule: IgnoreRule) -> str:
    if rule.kind == "torrent":
        return f"Torrent {rule.label}"
    if rule.kind == "file":
        return f"Fichier {rule.label}"
    return f"Alerte « {rule.label} »"


def _media_by_key(session: Session) -> dict[str, Media]:
    return {media_key(m): m for m in session.exec(select(Media)).all()}


def _media_of(session: Session, rule: IgnoreRule, by_key: dict[str, Media]) -> Media | None:
    """Média actuellement concerné par la règle, s'il existe encore."""
    if rule.kind == "torrent":
        torrent = session.exec(select(Torrent).where(func.lower(col(Torrent.hash)) == rule.target)).first()
        return session.get(Media, torrent.media_id) if torrent and torrent.media_id else None
    if rule.kind == "file":
        file = session.exec(select(MediaFile).where(col(MediaFile.path) == rule.target)).first()
        return session.get(Media, file.media_id) if file else None
    return by_key.get(rule.media_key)
