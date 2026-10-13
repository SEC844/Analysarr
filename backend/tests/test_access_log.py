"""Journal d'accès sans le bruit des healthchecks réussis (issue #51)."""

import logging
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn

from app.access_log import QuietHealthChecks, install_access_log_filter
from app.main import app
from tests.test_concurrency import _free_port


def access_record(path: str, status: int) -> logging.LogRecord:
    """Ligne telle qu'uvicorn la produit (h11_impl / httptools_impl)."""
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:39910", "GET", path, "1.1", status),
        None,
    )


@pytest.mark.parametrize(
    ("path", "status", "kept"),
    [
        ("/api/health", 200, False),
        ("/api/health?probe=1", 200, False),
        # Un healthcheck en échec est exactement ce qu'on veut lire.
        ("/api/health", 503, True),
        ("/api/health", 500, True),
        ("/api/healthz", 200, True),
        ("/api/media", 200, True),
        ("/api/status", 200, True),
    ],
)
def test_only_successful_health_checks_are_dropped(path, status, kept):
    assert QuietHealthChecks().filter(access_record(path, status)) is kept


def test_an_unexpected_line_is_always_kept():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, "%s", ("/api/health",), None)
    assert QuietHealthChecks().filter(record) is True


def test_the_filter_is_installed_once():
    logger = logging.getLogger("uvicorn.access")
    install_access_log_filter()
    install_access_log_filter()
    assert sum(isinstance(existing, QuietHealthChecks) for existing in logger.filters) == 1


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def access_lines() -> Iterator[tuple[str, list[str]]]:
    """Vrai uvicorn, journal d'accès activé : vérifie le filtre sur les lignes
    réellement produites, pas sur une imitation."""
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="info")
    server = uvicorn.Server(config)
    collector = _Collect()
    logger = logging.getLogger("uvicorn.access")
    install_access_log_filter()
    logger.addHandler(collector)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn n'a pas démarré")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", collector.lines
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        logger.removeHandler(collector)


def test_a_real_server_no_longer_logs_successful_health_checks(access_lines):
    base_url, lines = access_lines
    with httpx.Client(base_url=base_url) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/auth/status").status_code == 200

    deadline = time.monotonic() + 2
    while not any("/api/auth/status" in line for line in lines) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert any('"GET /api/auth/status HTTP/1.1" 200' in line for line in lines)
    assert not any("/api/health" in line for line in lines)
