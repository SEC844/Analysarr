"""État de chaque source du temps réel, affiché à côté du bouton Scanner. En
mémoire : un redémarrage repart de « en attente », ce qui est exact (rien n'a
encore été reçu ni vérifié). Chaque changement d'état est annoncé aux
navigateurs (`realtime.status`) : ils n'ont pas à le relire en boucle."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal

from app.services.events import live_events

# `active` : la source fonctionne (webhook reçu, interrogation réussie) ;
# `waiting` : branchée, rien reçu ni vérifié depuis le démarrage ; `error` :
# dernier essai en échec (message dans `error`).
SourceState = Literal["active", "waiting", "error"]

# Messages d'erreur tronqués : jamais de réponse complète d'un service.
_MAX_ERROR = 300


@dataclass(frozen=True)
class SourceStatus:
    key: str
    # webhook | torrents | media_server
    kind: str
    state: SourceState = "waiting"
    last_event_at: datetime | None = None
    last_check_at: datetime | None = None
    error: str | None = None


class StatusBoard:
    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self._sources: dict[str, SourceStatus] = {}
        self._on_change = on_change

    def _store(self, status: SourceStatus) -> None:
        """Annonce seulement ce qui se voit : un état ou un message d'erreur
        qui change, jamais une simple vérification de plus."""
        previous = self._sources.get(status.key)
        self._sources[status.key] = status
        changed = previous is None or (previous.state, previous.error) != (status.state, status.error)
        if changed and self._on_change is not None:
            self._on_change()

    def register(self, key: str, kind: str) -> None:
        if key not in self._sources:
            self._sources[key] = SourceStatus(key=key, kind=kind)

    def forget(self, key: str) -> None:
        self._sources.pop(key, None)

    def checked(self, key: str) -> None:
        """Vérification réussie (interrogation sans erreur)."""
        current = self._sources.get(key)
        if current is not None:
            self._store(replace(current, state="active", last_check_at=_now(), error=None))

    def event(self, key: str) -> None:
        """Changement reçu ou détecté."""
        current = self._sources.get(key)
        if current is not None:
            now = _now()
            self._store(replace(current, state="active", last_event_at=now, last_check_at=now, error=None))

    def failed(self, key: str, error: str) -> None:
        current = self._sources.get(key)
        if current is not None:
            self._store(replace(current, state="error", last_check_at=_now(), error=error[:_MAX_ERROR]))

    def get(self, key: str) -> SourceStatus | None:
        return self._sources.get(key)

    def snapshot(self) -> list[SourceStatus]:
        return sorted(self._sources.values(), key=lambda s: s.key)

    def clear(self) -> None:
        self._sources.clear()


def _now() -> datetime:
    return datetime.now(UTC)


def _announce() -> None:
    live_events.publish_nowait(STATUS_EVENT)


STATUS_EVENT: dict[str, Any] = {"type": "realtime.status"}
board = StatusBoard(on_change=_announce)
