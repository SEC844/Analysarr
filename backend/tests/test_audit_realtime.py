"""Non-régression des constats de l'audit de la 1.0.3 (temps réel)."""

import asyncio

import httpx
import pytest

from app.services.realtime import watchers
from app.services.realtime.webhooks import forget_deliveries


class FakeHub:
    def __init__(self) -> None:
        self.media: list[int] = []
        self.services: list[str] = []

    def media_changed(self, media_id: int, *, watch_only: bool = False) -> None:
        self.media.append(media_id)

    def service_changed(self, scope: str) -> None:
        self.services.append(scope)


class FakeBoard:
    def register(self, *_): ...
    def checked(self, *_): ...
    def event(self, *_): ...
    def failed(self, *_): ...
    def get(self, *_):
        return None


# 11. Un guetteur qui se reconnecte reprend son état d'avant la coupure : ce
#     qui a changé pendant la coupure est signalé.
def test_torrent_changes_during_an_outage_are_seen_after_reconnecting(settings, monkeypatch):
    complete = ("t", None, None, None, True)
    script = [
        {"aaa": complete},  # session 1 : état de référence
        httpx.ConnectError("qBittorrent redémarre"),  # coupure (Gluetun recréé)
        {"aaa": complete, "bbb": complete},  # session 2 : bbb ajouté pendant la coupure
        {"aaa": complete, "bbb": complete},
        asyncio.CancelledError(),
    ]

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def fingerprints(self):
            step = script.pop(0)
            if isinstance(step, BaseException):
                raise step
            return step

    async def no_sleep(_):
        return None

    monkeypatch.setattr(watchers, "torrent_client", lambda _s: FakeClient())
    monkeypatch.setattr(watchers.asyncio, "sleep", no_sleep)
    hub = FakeHub()
    watcher = watchers.TorrentWatcher(settings, 0, hub, FakeBoard())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(watcher.run())
    # Le torrent ajouté pendant la coupure est vu dès la reconnexion.
    assert hub.services == ["torrents"] and hub.media == []


def test_arr_history_during_an_outage_is_caught_up_after_reconnecting(settings, monkeypatch):
    """Sonarr redémarré : l'import survenu pendant la coupure est repris."""
    forget_deliveries()  # aucun webhook reçu dans ce scénario
    script = [
        [{"id": 10, "seriesId": 3, "eventType": "grabbed"}],  # référence
        httpx.ConnectError("Sonarr redémarre"),
        [{"id": 11, "seriesId": 7, "eventType": "downloadFolderImported"}, {"id": 10, "seriesId": 3}],
        asyncio.CancelledError(),
    ]

    class FakeArr:
        async def get_recent_history(self, _size):
            step = script.pop(0)
            if isinstance(step, BaseException):
                raise step
            return step

        async def get_queue_status(self):
            return {"totalCount": 0}

    class Target:
        kind, instance_id, name = "sonarr", None, "Sonarr"

        def sonarr(self):
            return FakeArr()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(watchers.asyncio, "sleep", no_sleep)
    hub = FakeHub()
    watcher = watchers.ArrWatcher(settings, 0, hub, FakeBoard(), Target())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(watcher.run())
    # Série inconnue importée pendant la coupure : le service est relu.
    assert hub.services == ["sonarr"]
