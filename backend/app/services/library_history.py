"""Historique de la bibliothèque : une photographie par jour de sa taille et
de l'espace des disques qui la portent — la matière des graphiques et des
prévisions de remplissage.

Une ligne par jour (UTC, comme toutes les dates stockées), écrite par un job
quotidien et après chaque scan complet. L'écriture est un « upsert » sur le
jour : relancer le même jour remplace la mesure précédente, la dernière gagne.

Deux règles pour ne jamais enregistrer un chiffre faux, qui fausserait ensuite
toutes les prévisions :
- aucune photographie tant qu'aucun scan complet n'a abouti (première
  installation, ou cache média recréé après une mise à jour) : la bibliothèque
  paraîtrait vide, puis bondirait au scan suivant ;
- une racine non montée (path_guard) est notée indisponible, sans chiffres :
  `disk_usage` mesurerait sinon le disque système du conteneur.

Lecture seule sur le disque : `os.stat` et `shutil.disk_usage`, rien d'autre."""

import logging
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, col, delete, select

from app.database import engine
from app.models.library_snapshot import LibrarySnapshot
from app.models.media import Media, MediaType, ScanRun, ScanStatus
from app.models.settings import Settings
from app.services.path_guard import root_unavailable

logger = logging.getLogger(__name__)

# Détail quotidien conservé deux ans par défaut : assez pour voir une
# saisonnalité d'une année sur l'autre, pour un poids négligeable (une ligne
# par jour).
DEFAULT_RETENTION_DAYS = 730
MIN_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 3650

DiskRole = Literal["library", "downloads"]


class DiskUsage(BaseModel):
    """Espace d'un disque, vu depuis une racine configurée. `device`
    (identifiant du système de fichiers) permet de ne pas compter deux fois un
    même disque qui porte à la fois la bibliothèque et les téléchargements
    (le cas recommandé pour les hardlinks)."""

    role: DiskRole
    path: str
    available: bool
    total: int | None = None
    free: int | None = None
    device: int | None = None


_DISKS = TypeAdapter(list[DiskUsage])


def retention_days(settings: Settings | None) -> int:
    value = settings.library_history_retention_days if settings else DEFAULT_RETENTION_DAYS
    return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, value))


def today() -> date:
    return datetime.now(UTC).date()


# --- Mesures ------------------------------------------------------------------


def _roots(settings: Settings | None) -> list[tuple[DiskRole, str]]:
    if settings is None:
        return []
    configured: list[tuple[DiskRole, str | None]] = [
        ("library", settings.emby_library_path),
        ("downloads", settings.qbittorrent_download_path),
    ]
    return [(role, path.strip()) for role, path in configured if path and path.strip()]


def measure_disk(role: DiskRole, path: str) -> DiskUsage:
    if root_unavailable(path):
        return DiskUsage(role=role, path=path, available=False)
    try:
        device = os.stat(path).st_dev
        usage = shutil.disk_usage(path)
    except OSError as exc:
        logger.warning("Espace disque illisible pour %s (%s)", path, type(exc).__name__)
        return DiskUsage(role=role, path=path, available=False)
    return DiskUsage(role=role, path=path, available=True, total=usage.total, free=usage.free, device=device)


def measure_disks(settings: Settings | None) -> list[DiskUsage]:
    return [measure_disk(role, path) for role, path in _roots(settings)]


@dataclass(frozen=True)
class LibraryTotals:
    movie_count: int = 0
    series_count: int = 0
    movie_size: int = 0
    series_size: int = 0


def library_totals(session: Session) -> LibraryTotals:
    """Une seule requête groupée : aucun média chargé en mémoire, même sur
    une bibliothèque de plusieurs dizaines de milliers d'éléments."""
    rows = session.exec(
        select(Media.media_type, func.count(), func.coalesce(func.sum(Media.total_size), 0)).group_by(
            col(Media.media_type)
        )
    ).all()
    counts = {media_type: (int(count), int(size)) for media_type, count, size in rows}
    movie_count, movie_size = counts.get(MediaType.movie, (0, 0))
    series_count, series_size = counts.get(MediaType.series, (0, 0))
    return LibraryTotals(movie_count, series_count, movie_size, series_size)


def library_is_known(session: Session) -> bool:
    """Au moins un scan complet a abouti depuis la création du cache média."""
    completed = select(ScanRun.id).where(col(ScanRun.status) == ScanStatus.completed, col(ScanRun.scope) == "full")
    return session.exec(completed.limit(1)).first() is not None


# --- Écriture -----------------------------------------------------------------


def take_snapshot(session: Session, settings: Settings | None, day: date | None = None) -> bool:
    """Enregistre la photographie du jour (remplace celle déjà prise ce jour-là)
    puis applique la rétention. Renvoie False si la bibliothèque n'est pas
    encore connue (aucun scan complet abouti)."""
    if not library_is_known(session):
        return False
    day = day or today()
    totals = library_totals(session)
    values = {
        "day": day,
        "taken_at": datetime.now(UTC).replace(tzinfo=None),
        "media_count": totals.movie_count + totals.series_count,
        "movie_count": totals.movie_count,
        "series_count": totals.series_count,
        "total_size": totals.movie_size + totals.series_size,
        "movie_size": totals.movie_size,
        "series_size": totals.series_size,
        "disks": _DISKS.dump_json(measure_disks(settings)).decode(),
    }
    # Upsert atomique : un job quotidien et la fin d'un scan qui tombent au
    # même moment ne peuvent jamais produire deux lignes pour un même jour.
    statement = insert(LibrarySnapshot).values(**values)
    session.exec(
        statement.on_conflict_do_update(
            index_elements=[col(LibrarySnapshot.day)],
            set_={key: statement.excluded[key] for key in values if key != "day"},
        )
    )
    purge_expired(session, settings, day)
    session.commit()
    return True


def purge_expired(session: Session, settings: Settings | None, day: date | None = None) -> None:
    """Ne garde que les `retention_days` derniers jours, jour courant compris.
    Sans commit : appelée dans la transaction de la photographie."""
    cutoff = (day or today()) - timedelta(days=retention_days(settings) - 1)
    session.exec(delete(LibrarySnapshot).where(col(LibrarySnapshot.day) < cutoff))


def record_snapshot() -> None:
    """Point d'entrée des tâches de fond (job quotidien, fin de scan) : jamais
    d'exception — une mesure manquée ne doit ni interrompre un scan ni
    arrêter le planificateur."""
    try:
        with Session(engine) as session:
            take_snapshot(session, session.get(Settings, 1))
    except Exception:  # noqa: BLE001 - voir docstring : journalisé, jamais propagé
        logger.warning("Photographie quotidienne de la bibliothèque impossible", exc_info=True)


def record_missing_snapshot() -> None:
    """Au démarrage : un conteneur éteint au moment du job quotidien ne laisse
    pas de trou, mais une mesure déjà prise ce jour-là n'est pas refaite."""
    try:
        with Session(engine) as session:
            taken = select(LibrarySnapshot.id).where(col(LibrarySnapshot.day) == today())
            if session.exec(taken).first() is None:
                take_snapshot(session, session.get(Settings, 1))
    except Exception:  # noqa: BLE001 - voir record_snapshot
        logger.warning("Photographie de la bibliothèque au démarrage impossible", exc_info=True)


# --- Lecture ------------------------------------------------------------------


def history(session: Session, days: int, day: date | None = None) -> list[LibrarySnapshot]:
    """Les `days` derniers jours, du plus ancien au plus récent."""
    since = (day or today()) - timedelta(days=days - 1)
    query = select(LibrarySnapshot).where(col(LibrarySnapshot.day) >= since).order_by(col(LibrarySnapshot.day))
    return list(session.exec(query).all())


def disks_of(snapshot: LibrarySnapshot) -> list[DiskUsage]:
    try:
        return _DISKS.validate_json(snapshot.disks)
    except ValidationError:
        logger.warning("Mesure des disques illisible pour le %s", snapshot.day)
        return []
