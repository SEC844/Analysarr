from datetime import UTC, date, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    # Naïf (UTC), comme le reste des tables : SQLite relit sans fuseau.
    return datetime.now(UTC).replace(tzinfo=None)


class LibrarySnapshot(SQLModel, table=True):
    """Photographie quotidienne de la bibliothèque et des disques (voir
    services/library_history.py) : la matière des graphiques et des prévisions
    d'espace disque.

    Table de DONNÉES, jamais vidée avec le cache média : l'historique ne se
    reconstruit pas. Une ligne par jour (UTC) ; la dernière mesure du jour
    remplace les précédentes."""

    id: int | None = Field(default=None, primary_key=True)
    day: date = Field(unique=True, index=True)
    taken_at: datetime = Field(default_factory=_utcnow)

    media_count: int = 0
    movie_count: int = 0
    series_count: int = 0

    # Somme de `Media.total_size` (fichiers actuellement suivis, hors
    # doublons), en octets — le même chiffre que les cartes de la bibliothèque.
    total_size: int = 0
    movie_size: int = 0
    series_size: int = 0

    # Liste JSON, une entrée par dossier racine configuré (voir
    # library_history.DiskUsage) : rôle, chemin, disponibilité, espace total et
    # libre, identifiant du système de fichiers.
    disks: str = "[]"
