"""État de chaque source du temps réel, affiché dans Réglages → Temps réel et
à côté du bouton Scanner. En mémoire : un redémarrage repart de « en
attente », ce qui est exact (rien n'a encore été reçu ni vérifié)."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

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
    def __init__(self) -> None:
        self._sources: dict[str, SourceStatus] = {}

    def register(self, key: str, kind: str) -> None:
        if key not in self._sources:
            self._sources[key] = SourceStatus(key=key, kind=kind)

    def forget(self, key: str) -> None:
        self._sources.pop(key, None)

    def checked(self, key: str) -> None:
        """Vérification réussie (interrogation sans erreur)."""
        current = self._sources.get(key)
        if current is not None:
            self._sources[key] = replace(current, state="active", last_check_at=_now(), error=None)

    def event(self, key: str) -> None:
        """Changement reçu ou détecté."""
        current = self._sources.get(key)
        if current is not None:
            now = _now()
            self._sources[key] = replace(current, state="active", last_event_at=now, last_check_at=now, error=None)

    def failed(self, key: str, error: str) -> None:
        current = self._sources.get(key)
        if current is not None:
            self._sources[key] = replace(current, state="error", last_check_at=_now(), error=error[:_MAX_ERROR])

    def get(self, key: str) -> SourceStatus | None:
        return self._sources.get(key)

    def snapshot(self) -> list[SourceStatus]:
        return sorted(self._sources.values(), key=lambda s: s.key)

    def clear(self) -> None:
        self._sources.clear()


def _now() -> datetime:
    return datetime.now(UTC)


board = StatusBoard()
