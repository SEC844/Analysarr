"""Quel média un changement concerne-t-il ? Fonctions synchrones (base de
données) : appelées par `run_in_threadpool`, jamais dans la boucle asyncio.

Seuls les identifiants servent : le reste de ce que dit une source (webhook,
client torrent, serveur multimédia) n'est jamais cru, la vérité est relue
par l'analyse du média.

Un média est désigné par son identité STABLE (`ignores.media_key`), jamais par
son identifiant en base : un scan complet régénère les identifiants pendant
qu'un signal attend dans la file (bug réel évité : le signal « Dune importé »
reçu pendant le scan de nuit faisait relire un autre film). La file résout
la clé en identifiant seulement une fois le verrou d'analyse pris
(`media_ids_for_keys`)."""

from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy import select as core_select
from sqlmodel import Session, col, select

from app.database import engine
from app.models.media import Media, MediaType, Torrent
from app.services.ignores import media_key_of


@dataclass
class Resolved:
    """Médias connus (clés stables), et identifiants que personne ne porte
    encore (nouveau film, nouveau torrent) : une analyse du service concerné
    les rattachera."""

    media_keys: set[str] = field(default_factory=set)
    unknown: set[str] = field(default_factory=set)


_KEY_COLUMNS = (
    col(Media.media_type),
    col(Media.sonarr_id),
    col(Media.radarr_id),
    col(Media.arr_instance_id),
    col(Media.emby_item_id),
)


def _key(
    media_type: MediaType, sonarr_id: int | None, radarr_id: int | None, instance: int | None, item: str | None
) -> str:
    arr_id = sonarr_id if media_type == MediaType.series else radarr_id
    return media_key_of(media_type, arr_id, instance, item)


def _arr_key(service: str, instance_id: int | None, arr_id: int) -> str:
    media_type = MediaType.series if service == "sonarr" else MediaType.movie
    return media_key_of(media_type, arr_id, instance_id, None)


def media_for_arr(service: str, instance_id: int | None, arr_id: int) -> str | None:
    """Média suivi par cette fiche Sonarr/Radarr (`instance_id` None =
    instance principale)."""
    return media_for_arr_ids(service, instance_id, [arr_id]).get(arr_id)


def media_for_arr_ids(service: str, instance_id: int | None, arr_ids: list[int]) -> dict[int, str]:
    """Médias suivis par ces fiches Sonarr/Radarr : identifiant arr → média."""
    if not arr_ids:
        return {}
    column = col(Media.sonarr_id) if service == "sonarr" else col(Media.radarr_id)
    instance = col(Media.arr_instance_id)
    with Session(engine) as session:
        rows = session.exec(
            select(column).where(
                column.in_(arr_ids), instance.is_(None) if instance_id is None else instance == instance_id
            )
        ).all()
    return {arr_id: _arr_key(service, instance_id, arr_id) for arr_id in rows if arr_id is not None}


def media_for_hashes(hashes: Iterable[str]) -> Resolved:
    wanted = {h.lower() for h in hashes if h}
    resolved = Resolved()
    if not wanted:
        return resolved
    with Session(engine) as session:
        # Hashes stockés tels que le client les donne : comparaison sans casse.
        rows = session.execute(
            core_select(col(Torrent.hash), *_KEY_COLUMNS)
            .join(Media, col(Media.id) == col(Torrent.media_id))
            .where(func.lower(col(Torrent.hash)).in_(list(wanted)))
        ).all()
    resolved.media_keys = {_key(*row[1:]) for row in rows}
    # Un torrent connu mais rattaché à aucun média reste « inconnu » : seule
    # une analyse du client torrent peut le rattacher.
    resolved.unknown = wanted - {row[0].lower() for row in rows}
    return resolved


def media_for_items(item_ids: Iterable[str]) -> Resolved:
    """Éléments du serveur multimédia : un film ou une série (l'épisode est
    ramené à sa série par l'appelant)."""
    wanted = {i for i in item_ids if i}
    resolved = Resolved()
    if not wanted:
        return resolved
    with Session(engine) as session:
        rows = session.execute(core_select(*_KEY_COLUMNS).where(col(Media.emby_item_id).in_(list(wanted)))).all()
    resolved.media_keys = {_key(*row) for row in rows}
    resolved.unknown = wanted - {row[4] for row in rows if row[4]}
    return resolved


def media_ids_for_keys(keys: Iterable[str]) -> dict[str, int]:
    """Identifiant ACTUEL de chaque média désigné par sa clé stable ; une clé
    qui ne désigne plus rien (média retiré) est absente du résultat."""
    wanted = set(keys)
    if not wanted:
        return {}
    with Session(engine) as session:
        rows = session.execute(core_select(col(Media.id), *_KEY_COLUMNS)).all()
    found: dict[str, int] = {}
    for media_id, *columns in rows:
        key = _key(*columns)
        if key in wanted and media_id is not None:
            found.setdefault(key, media_id)
    return found
