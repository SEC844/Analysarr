"""Chaque module doit s'importer SEUL, dans un interpréteur neuf.

Bug réel : `services/ignores.py` importe `scan.statuses`, donc le package
`scan`, dont le déroulé importe à son tour les éléments ignorés. Les tests,
qui chargent d'abord `app.main`, ne voyaient rien ; un import direct de
`media_status` échouait (import circulaire)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

MODULES = [
    "app.services.ignores",
    "app.services.ignore_rules",
    "app.services.media_status",
    "app.services.scan.statuses",
    "app.services.scan",
    "app.services.cascade_delete",
    "app.services.media_rescan",
    "app.services.partial_scan",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_on_its_own(module, tmp_path):
    # Commande fixe : l'interpréteur courant et un module de la liste ci-dessus.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", f"import {module}"],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_PATH": str(tmp_path / "db.sqlite"), "PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
