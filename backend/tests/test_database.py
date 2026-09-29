"""Réglages du moteur SQLite (database.py) : mode WAL, attente des verrous,
pool dimensionné, et journal des attentes de connexion."""

import logging
import sqlite3

from sqlalchemy import create_engine, event, text

from app import database
from app.database import engine, request_path


def test_each_connection_uses_wal_and_waits_for_locks():
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 15000
        assert connection.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL


def test_the_pool_outgrows_the_thread_pool():
    """Plus de connexions que de threads d'anyio (40) : une route synchrone ne
    peut jamais attendre la dernière connexion (voir database.py)."""
    assert engine.pool.size() + engine.pool._max_overflow > 40  # noqa: SLF001


def test_a_slow_checkout_is_logged_with_the_path_only(monkeypatch, caplog):
    monkeypatch.setattr(database, "SLOW_CHECKOUT_SECONDS", -1.0)
    token = request_path.set("/api/media/3/poster")
    try:
        with caplog.at_level(logging.WARNING, logger="app.database"), engine.connect():
            pass
    finally:
        request_path.reset(token)

    [record] = [r for r in caplog.records if r.name == "app.database"]
    assert "/api/media/3/poster" in record.getMessage()
    assert "cookie" not in record.getMessage().lower() and "?" not in record.getMessage()


def test_an_existing_database_switches_to_wal_without_losing_data(tmp_path):
    """Base d'une installation existante, en mode de journal classique : elle
    passe en WAL à la première connexion, données intactes."""
    path = tmp_path / "analysarr.db"
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, language VARCHAR)")
    legacy.execute("INSERT INTO settings VALUES (1, 'fr')")
    legacy.commit()
    assert legacy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    legacy.close()

    upgraded = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    event.listen(upgraded, "connect", database._sqlite_pragmas)  # noqa: SLF001
    with upgraded.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("SELECT language FROM settings")).scalar() == "fr"
    upgraded.dispose()
