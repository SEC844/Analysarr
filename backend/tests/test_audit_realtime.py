"""Non-régression des constats de l'audit de la 1.0.3 (temps réel)."""

import asyncio

import httpx
import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import delete

from app.models.media import Media, MediaType
from app.services import media_rescan
from app.services.ignores import media_key
from app.services.realtime import hub as hub_module
from app.services.realtime import watchers
from app.services.realtime.hub import ANALYSIS_SOURCE, RealtimeHub
from app.services.realtime.status import board
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


# 14. Une erreur imprévue sur un média (« database is locked ») ne fait plus
#     sortir le lot du verrou pendant qu'une autre analyse écrit encore ; le
#     média en échec est remis en file et le retard est affiché.
def test_a_failed_analysis_keeps_the_lock_until_the_batch_ends(session, settings, monkeypatch):
    from app.services import scan as scan_package

    state = {}

    async def fake_rescan(session, settings, media):
        if media.title == "A":
            raise OperationalError("UPDATE media", {}, Exception("database is locked"))
        await asyncio.sleep(0.2)  # B écrit encore
        state["lock_held_at_end_of_B"] = scan_package._analysis.lock.locked()
        return media_rescan.MediaRescanResult()

    monkeypatch.setattr(media_rescan, "rescan_media", fake_rescan)
    medias = [Media(media_type=MediaType.movie, title=t, radarr_id=i) for i, t in enumerate(("A", "B"), 1)]
    session.add_all(medias)
    session.commit()
    hub = RealtimeHub()
    board.clear()

    asyncio.run(hub.process({media_key(m): True for m in medias}, []))

    assert state["lock_held_at_end_of_B"] is True
    failed = media_key(medias[0])
    # A attend un nouvel essai (échéance décalée du premier délai), B non.
    assert set(hub._media) == {failed} and hub._attempts == {failed: 1}
    due = hub._media[failed].first_seen + hub.debounce
    assert due - hub_module.time.monotonic() > hub_module.RETRY_DELAYS[0] - 5
    status = board.get(ANALYSIS_SOURCE)
    assert status is not None and status.state == "error" and "database is locked" in (status.error or "")

    # Nouvel essai réussi : plus de retard affiché.
    monkeypatch.setattr(media_rescan, "rescan_media", lambda *_: _ok())
    asyncio.run(hub.process({failed: True}, []))
    assert hub._attempts == {} and board.get(ANALYSIS_SOURCE) is None
    board.clear()


async def _ok():
    return media_rescan.MediaRescanResult()


def test_an_analysis_that_keeps_failing_is_eventually_left_to_the_nightly_scan(session, settings, monkeypatch):
    async def broken(session, settings, media):
        raise RuntimeError("Sonarr injoignable")

    monkeypatch.setattr(media_rescan, "rescan_media", broken)
    media = Media(media_type=MediaType.movie, title="A", radarr_id=1)
    session.add(media)
    session.commit()
    hub = RealtimeHub()

    for _ in range(len(hub_module.RETRY_DELAYS) + 1):
        hub._media.clear()
        asyncio.run(hub.process({media_key(media): True}, []))

    assert hub._attempts == {} and hub._media == {}
    board.clear()


# 13. Un signal mis en file AVANT un scan complet désigne toujours le bon média
#     APRÈS, même si le scan a régénéré les identifiants.
def test_a_queued_signal_still_targets_the_right_media_after_a_full_scan(session, settings, monkeypatch):
    seen: list[str] = []

    async def fake_rescan(session, settings, media):
        seen.append(media.title)
        return media_rescan.MediaRescanResult()

    monkeypatch.setattr(media_rescan, "rescan_media", fake_rescan)
    dune = Media(media_type=MediaType.movie, title="Dune", radarr_id=1)
    alien = Media(media_type=MediaType.movie, title="Alien", radarr_id=2)
    session.add_all([dune, alien])
    session.commit()
    queued = media_key(dune)  # webhook « Dune importé » reçu pendant le scan de nuit

    # _replace_cache : tout est effacé puis réinséré (ordre du nouveau scan)
    session.exec(delete(Media))
    session.commit()
    session.add_all(
        [
            Media(media_type=MediaType.movie, title="Alien", radarr_id=2),
            Media(media_type=MediaType.movie, title="Dune", radarr_id=1),
        ]
    )
    session.commit()

    asyncio.run(RealtimeHub().process({queued: True}, []))
    assert seen == ["Dune"]
