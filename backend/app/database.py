from pathlib import Path
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import DATABASE_PATH

db_path = Path(DATABASE_PATH)
db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{db_path}",
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    from app.models.media import Media, MediaFile, ScanRun, Torrent  # noqa: F401
    from app.models.settings import Settings  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
