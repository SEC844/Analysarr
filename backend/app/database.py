from pathlib import Path
from typing import Iterator

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.config import DATABASE_PATH

db_path = Path(DATABASE_PATH)
db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{db_path}",
    connect_args={"check_same_thread": False},
)

# (table, colonne) présente dès que ce déploiement a le schéma le plus récent
# pour cette table. Sert uniquement à détecter un schéma obsolète, voir
# _reset_media_cache_if_stale() — ajouter une entrée à chaque nouvelle colonne
# ajoutée sur Media/MediaFile/Torrent/ScanRun.
_CURRENT_SCHEMA_MARKERS = [
    ("torrent", "ratio"),
    ("scanrun", "qbittorrent_torrent_count"),
    ("torrent", "matched_by_name"),
]


def _reset_media_cache_if_stale() -> None:
    """Media/MediaFile/Torrent/ScanRun sont un pur cache reconstruit à chaque
    scan : ce projet n'a pas de migrations Alembic, et `create_all()` ne
    modifie jamais une table déjà existante. Si le schéma attendu a changé
    (nouvelle colonne sur l'une de ces tables), on les recrée plutôt que de
    gérer des ALTER TABLE colonne par colonne — sans jamais toucher
    `settings`, qui contient la vraie configuration de l'utilisateur."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "torrent" not in table_names:
        return  # première installation : create_all() suffira à tout créer

    is_current = True
    for table, marker_column in _CURRENT_SCHEMA_MARKERS:
        if table not in table_names:
            is_current = False
            break
        existing_columns = {col["name"] for col in inspector.get_columns(table)}
        if marker_column not in existing_columns:
            is_current = False
            break
    if is_current:
        return

    with engine.begin() as conn:
        for table in ("torrent", "mediafile", "scanrun", "media"):
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))


def init_db() -> None:
    from app.models.media import Media, MediaFile, ScanRun, Torrent  # noqa: F401
    from app.models.settings import Settings  # noqa: F401

    _reset_media_cache_if_stale()
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
