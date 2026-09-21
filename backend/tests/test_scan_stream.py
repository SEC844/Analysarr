import asyncio

from app.routers import scan as scan_router
from app.services.events import scan_events


def test_stream_opens_immediately_and_is_never_buffered_by_a_proxy(monkeypatch):
    monkeypatch.setattr(scan_router, "STREAM_HEARTBEAT_SECONDS", 0.01)

    async def run():
        response = await scan_router.scan_stream()
        chunks = response.body_iterator
        first = await chunks.__anext__()
        ping = await chunks.__anext__()
        await scan_events.publish({"type": "completed", "run_id": 1})
        rest = [chunk async for chunk in chunks]
        return response, first, ping, rest

    response, first, ping, rest = asyncio.run(run())

    # Premier octet avant tout événement : l'ouverture du flux traverse un proxy qui met en tampon.
    assert first == ": connected\n\n"
    assert ping == ": ping\n\n"
    assert rest[-1] == 'data: {"type": "completed", "run_id": 1}\n\n'
    assert response.headers["x-accel-buffering"] == "no"
    assert "no-transform" in response.headers["cache-control"]
    assert scan_events._subscribers == []


def test_started_scan_task_is_kept_until_it_finishes(monkeypatch):
    started = []

    async def fake_run_scan(scope="full"):
        started.append(scope)

    monkeypatch.setattr(scan_router, "run_scan", fake_run_scan)
    monkeypatch.setattr(scan_router, "is_scan_running", lambda: False)

    async def run():
        result = await scan_router.start_scan(scope="full")
        assert len(scan_router._running_tasks) == 1
        await asyncio.gather(*scan_router._running_tasks)
        await asyncio.sleep(0)  # laisse passer le callback de fin de tâche
        return result

    assert asyncio.run(run()) == {"started": True}
    assert started == ["full"] and not scan_router._running_tasks


def test_an_unknown_scope_is_refused(monkeypatch):
    """Liste fermée : un périmètre inventé ne doit jamais lancer d'analyse."""
    import pytest
    from fastapi import HTTPException

    monkeypatch.setattr(scan_router, "is_scan_running", lambda: False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(scan_router.start_scan(scope="../etc/passwd"))
    assert exc.value.status_code == 400
    assert not scan_router._running_tasks


def test_each_supported_scope_starts_a_scan(monkeypatch):
    started = []

    async def fake_run_scan(scope="full"):
        started.append(scope)

    monkeypatch.setattr(scan_router, "run_scan", fake_run_scan)
    monkeypatch.setattr(scan_router, "is_scan_running", lambda: False)

    from app.services.scan_scopes import SCAN_SCOPES

    async def run():
        for scope in SCAN_SCOPES:
            await scan_router.start_scan(scope=scope)
        await asyncio.gather(*scan_router._running_tasks)

    asyncio.run(run())
    assert started == list(SCAN_SCOPES)
