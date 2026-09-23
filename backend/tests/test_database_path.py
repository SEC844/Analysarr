"""Emplacement de la base : une mise à jour de l'image ne doit jamais faire
repartir une installation existante de zéro."""

from app.config import (
    CONTAINER_DATABASE_PATH,
    LEGACY_DATABASE_PATH,
    LOCAL_DATABASE_PATH,
    resolve_database_path,
)


def existing(*paths):
    return lambda path: path in paths


def test_an_explicit_path_always_wins():
    assert resolve_database_path("/srv/db.sqlite", existing(LEGACY_DATABASE_PATH)) == "/srv/db.sqlite"


def test_a_new_container_uses_config():
    assert resolve_database_path(None, existing("/config")) == CONTAINER_DATABASE_PATH


def test_an_existing_install_keeps_its_database_in_data(caplog):
    path = resolve_database_path(None, existing("/config", LEGACY_DATABASE_PATH))

    assert path == LEGACY_DATABASE_PATH
    assert "ancien emplacement" in caplog.text


def test_a_migrated_install_prefers_config():
    """Base copiée dans /config : l'ancienne copie dans /data est ignorée."""
    paths = existing("/config", CONTAINER_DATABASE_PATH, LEGACY_DATABASE_PATH)

    assert resolve_database_path(None, paths) == CONTAINER_DATABASE_PATH


def test_local_development_falls_back_to_the_project_folder():
    assert resolve_database_path(None, existing()) == LOCAL_DATABASE_PATH
