"""Éléments ignorés : un torrent, un fichier de bibliothèque gardé
volontairement, ou une alerte d'un média.

Une règle ne vaut que tant que la situation ignorée reste la même. À chaque
analyse qui voit la cible, sa situation actuelle est comparée à celle du
moment où elle a été ignorée (`IgnoreRule.fingerprint`) :

- identique : l'élément reste ignoré ;
- résolue (le torrent est désormais protégé, le doublon a disparu, l'alerte
  n'a plus lieu d'être) ou différente (autre torrent orphelin, autre fichier,
  autre téléchargement bloqué) : la règle est retirée, tracée dans
  l'historique, et l'alerte revient si le problème existe toujours.

Une situation qui ne peut pas être évaluée (chemins inaccessibles, torrent non
évalué) ne confirme ni ne retire rien : un disque démonté ne doit pas faire
revenir toutes les alertes masquées.

Les règles sont une table de configuration, jamais vidée avec le cache ; les
drapeaux `Torrent.ignored` / `MediaFile.ignored` et `Media.muted_statuses`,
eux, sont recalculés à chaque analyse à partir d'elles."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlmodel import Session, col, delete, select

from app.models.ignore import IgnoreRule
from app.models.media import Media, MediaFile, MediaType, Torrent
from app.services.action_log import MediaRef, record_action
from app.services.scan.statuses import INFO_STATUSES, MEDIA_STATUSES, StatusEvaluation, duplicate_groups

IGNORE_KINDS = ("torrent", "file", "status")
# Alertes qu'un utilisateur peut masquer : tout statut sauf les informations.
MUTABLE_STATUSES = tuple(status for status in MEDIA_STATUSES if status not in INFO_STATUSES)
# États d'un torrent qui déclenchent une alerte : seuls ceux-là s'ignorent.
IGNORABLE_TORRENT_STATES = ("orphan", "repairable")

_REASONS = {
    "resolved": "situation résolue, n'est plus ignoré",
    "changed": "situation changée, de nouveau signalé",
}


def media_key(media: Media) -> str:
    """Identité d'un média stable d'un scan à l'autre : les ids de la table
    sont régénérés à chaque scan complet. Même principe que la clé des
    analyses partielles (instance + id Sonarr/Radarr, ou item du serveur
    multimédia pour un média non suivi)."""
    arr_id = media.sonarr_id if media.media_type == MediaType.series else media.radarr_id
    if arr_id is None:
        return f"{media.media_type.value}:library:{media.emby_item_id or ''}"
    return f"{media.media_type.value}:arr:{media.arr_instance_id or 0}:{arr_id}"


def torrent_state(torrent: Torrent) -> str | None:
    """État d'un torrent vis-à-vis de sa bibliothèque, None s'il n'a pas pu
    être évalué."""
    if torrent.is_hardlinked is None:
        return None
    if torrent.is_hardlinked:
        return "protected"
    if torrent.repairable:
        return "repairable"
    return "not_imported" if torrent.not_imported else "orphan"


def duplicate_file_ids(files: Sequence[MediaFile]) -> set[int]:
    """Fichiers qui font ENCORE partie d'un doublon, une fois écartés ceux déjà
    gardés volontairement : seuls eux peuvent l'être. Une VF et une VOSTFR
    dont l'une est gardée ne forment plus de doublon — l'autre n'a rien à
    garder."""
    active = [f for f in files if not f.ignored]
    return {f.id for group in duplicate_groups(active) for f in group if f.id is not None}


def _fingerprint(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class _Rule:
    """Copie d'une règle, lisible après la fermeture de la session qui l'a
    chargée (le scan complet la lit avant de reconstruire le cache)."""

    id: int
    kind: str
    media_key: str
    media_title: str
    media_type: str
    target: str
    label: str
    fingerprint: str | None


@dataclass
class _Expired:
    rule: _Rule
    reason: str


@dataclass
class IgnoreSet:
    """Règles chargées une fois pour une analyse ou une action, appliquées
    média par média, puis enregistrées (`persist`)."""

    torrents: dict[str, _Rule] = field(default_factory=dict)
    files: dict[str, _Rule] = field(default_factory=dict)
    statuses: dict[str, list[_Rule]] = field(default_factory=dict)
    adopted: dict[int, str] = field(default_factory=dict)
    expired: list[_Expired] = field(default_factory=list)

    @classmethod
    def load(cls, session: Session) -> "IgnoreSet":
        ignores = cls()
        for row in session.exec(select(IgnoreRule)).all():
            rule = _Rule(
                id=row.id or 0,
                kind=row.kind,
                media_key=row.media_key,
                media_title=row.media_title,
                media_type=row.media_type,
                target=row.target,
                label=row.label,
                fingerprint=row.fingerprint,
            )
            if rule.kind == "torrent":
                ignores.torrents[rule.target] = rule
            elif rule.kind == "file":
                ignores.files[rule.target] = rule
            else:
                ignores.statuses.setdefault(rule.media_key, []).append(rule)
        return ignores

    # --- Application --------------------------------------------------------

    def mark(self, key: str, files: Sequence[MediaFile], torrents: Sequence[Torrent]) -> None:
        """Pose `ignored` sur les torrents et fichiers de ce média, AVANT le
        calcul des statuts."""
        for torrent in torrents:
            torrent.ignored = self._torrent_ignored(key, torrent)
        in_duplicate = {id(f) for group in duplicate_groups(files) for f in group}
        for f in files:
            f.ignored = self._file_ignored(key, f, id(f) in in_duplicate)

    def mute(self, key: str, evaluation: StatusEvaluation) -> set[str]:
        """Alertes masquées de ce média, APRÈS le calcul des statuts."""
        muted = set()
        for rule in list(self.statuses.get(key, [])):
            status = rule.target
            if status in evaluation.uncertain:
                if status in evaluation.statuses:
                    muted.add(status)
                continue
            if status not in evaluation.statuses:
                self._expire(rule, "resolved")
            elif self._holds(rule, f"{key}|{evaluation.situations.get(status, '')}"):
                muted.add(status)
        return muted

    def _torrent_ignored(self, key: str, torrent: Torrent) -> bool:
        rule = self.torrents.get((torrent.hash or "").lower())
        if rule is None:
            return False
        state = torrent_state(torrent)
        if state is None:
            return True  # non évalué : rien ne change
        if state not in IGNORABLE_TORRENT_STATES:
            self._expire(rule, "resolved")
            return False
        return self._holds(rule, f"{key}|{state}")

    def _file_ignored(self, key: str, file: MediaFile, in_duplicate: bool) -> bool:
        rule = self.files.get(file.path)
        if rule is None:
            return False
        if file.inode is None:
            return True  # fichier inaccessible : rien ne change
        if not in_duplicate:
            self._expire(rule, "resolved")
            return False
        return self._holds(rule, f"{key}|{file.episode_label or ''}|{file.size}")

    def _holds(self, rule: _Rule, situation: str) -> bool:
        """La situation est-elle toujours celle qui a été ignorée ? Une règle
        toute neuve adopte la situation actuelle."""
        current = _fingerprint(situation)
        if rule.fingerprint is None:
            rule.fingerprint = self.adopted[rule.id] = current
            return True
        if rule.fingerprint == current:
            return True
        self._expire(rule, "changed")
        return False

    def _expire(self, rule: _Rule, reason: str) -> None:
        if rule.kind == "torrent":
            self.torrents.pop(rule.target, None)
        elif rule.kind == "file":
            self.files.pop(rule.target, None)
        else:
            self.statuses[rule.media_key] = [r for r in self.statuses.get(rule.media_key, []) if r.id != rule.id]
        self.adopted.pop(rule.id, None)
        self.expired.append(_Expired(rule, reason))

    # --- Enregistrement -----------------------------------------------------

    def persist(self, session: Session) -> None:
        """Situations adoptées, règles retirées (tracées dans l'historique).
        Committe."""
        for rule_id, fingerprint in self.adopted.items():
            row = session.get(IgnoreRule, rule_id)
            if row is not None:
                row.fingerprint = fingerprint
                session.add(row)
        expired_ids = [expired.rule.id for expired in self.expired]
        if expired_ids:
            session.exec(delete(IgnoreRule).where(col(IgnoreRule.id).in_(expired_ids)))
        session.commit()
        for expired in self.expired:
            rule = expired.rule
            record_action(
                session,
                "ignore_expired",
                MediaRef(id=None, title=rule.media_title, media_type=rule.media_type),
                [_Step(f"{rule.label} : {_REASONS[expired.reason]}")],
            )
        self.adopted.clear()
        self.expired.clear()


@dataclass
class _Step:
    label: str
    success: bool = True
    error: str | None = None
