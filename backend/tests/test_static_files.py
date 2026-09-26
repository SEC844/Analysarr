"""Fichiers de l'interface : jamais rien hors du dossier du build.

Faille réelle : la route de repli servait `STATIC_DIR / chemin` sans
vérifier qu'il y restait. `/..%2F..%2Fconfig%2Fanalysarr.db` renvoyait, sans
session, la base et ses clés API."""

import pytest

from app.static_files import resolve_static_file


@pytest.fixture
def layout(tmp_path):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html></html>")
    (static / "favicon.svg").write_text("<svg/>")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "analysarr.db").write_text("secrets")
    return static


def test_a_file_of_the_build_is_served(layout):
    assert resolve_static_file(layout, "favicon.svg") == (layout / "favicon.svg").resolve()


@pytest.mark.parametrize(
    "requested",
    [
        "../config/analysarr.db",
        "assets/../../config/analysarr.db",
        "..\config\analysarr.db",
        "",
        "assets",  # un dossier n'est pas un fichier
        "absent.js",
    ],
)
def test_nothing_outside_the_build_is_ever_served(layout, requested):
    assert resolve_static_file(layout, requested) is None


def test_an_absolute_path_never_escapes(layout, tmp_path):
    assert resolve_static_file(layout, str(tmp_path / "config" / "analysarr.db")) is None
