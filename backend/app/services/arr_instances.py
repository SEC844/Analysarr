"""Instances Sonarr/Radarr : la principale (Settings) et les supplémentaires
(table ArrInstance). Un média garde l'instance qui le suit
(`Media.arr_instance_id`, None = instance principale) : toute action
Sonarr/Radarr sur ce média passe par `arr_target_for`, jamais par les champs
de Settings directement."""

from dataclasses import dataclass

from sqlmodel import Session, select

from app.clients.arr import RadarrClient, SonarrClient
from app.models.arr_instance import ArrInstance
from app.models.media import Media, MediaType
from app.models.settings import Settings

ARR_KINDS = ("sonarr", "radarr")
PRIMARY_NAMES = {"sonarr": "Sonarr", "radarr": "Radarr"}
MAX_EXTRA_INSTANCES = 10


@dataclass(frozen=True)
class ArrTarget:
    """Copie des paramètres d'une instance : utilisable hors session de base
    (scan en tâche de fond)."""

    kind: str
    instance_id: int | None
    name: str
    url: str
    api_key: str

    def radarr(self) -> RadarrClient:
        return RadarrClient(self.url, self.api_key)

    def sonarr(self) -> SonarrClient:
        return SonarrClient(self.url, self.api_key)


def _primary(settings: Settings | None, kind: str) -> ArrTarget | None:
    url = getattr(settings, f"{kind}_url", None) if settings is not None else None
    api_key = getattr(settings, f"{kind}_api_key", None) if settings is not None else None
    return ArrTarget(kind, None, PRIMARY_NAMES[kind], url, api_key) if url and api_key else None


def _extra(row: ArrInstance) -> ArrTarget:
    return ArrTarget(row.kind, row.id, row.name, row.url, row.api_key)


def extra_instances(session: Session, kind: str | None = None) -> list[ArrInstance]:
    query = select(ArrInstance).order_by(ArrInstance.id)
    if kind is not None:
        query = query.where(ArrInstance.kind == kind)
    return list(session.exec(query).all())


def arr_targets(session: Session, settings: Settings | None, kind: str) -> list[ArrTarget]:
    """Instance principale (si configurée) puis instances supplémentaires."""
    primary = _primary(settings, kind)
    return ([primary] if primary else []) + [_extra(row) for row in extra_instances(session, kind)]


def arr_target_for(session: Session, settings: Settings | None, media: Media) -> ArrTarget | None:
    """Instance qui suit ce média, ou None (non configurée, ou supprimée
    depuis le dernier scan)."""
    kind = "radarr" if media.media_type == MediaType.movie else "sonarr"
    if media.arr_instance_id is None:
        return _primary(settings, kind)
    row = session.get(ArrInstance, media.arr_instance_id)
    return _extra(row) if row is not None and row.kind == kind else None


def arr_target_by_id(session: Session, settings: Settings | None, kind: str, instance_id: int | None) -> ArrTarget | None:
    """Instance désignée par son identifiant (None = principale). Sert à la
    restauration depuis la corbeille, où le média n'existe plus en base."""
    if instance_id is None:
        return _primary(settings, kind)
    row = session.get(ArrInstance, instance_id)
    return _extra(row) if row is not None and row.kind == kind else None


def instance_names(session: Session) -> dict[int, str]:
    return {row.id: row.name for row in extra_instances(session)}
