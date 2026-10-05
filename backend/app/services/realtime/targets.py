"""Quel média un changement concerne-t-il ? Fonctions synchrones (base de
données) : appelées par `run_in_threadpool`, jamais dans la boucle asyncio.

Seuls les identifiants servent : le reste de ce que dit une source (webhook,
client torrent, serveur multimédia) n'est jamais cru, la vérité est relue
par l'analyse du média."""

from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.database import engine
from app.models.media import Media, Torrent


@dataclass
class Resolved:
    """Médias connus, et identifiants que personne ne porte encore (nouveau
    film, nouveau torrent) : une analyse du service concerné les rattachera."""

    media_ids: set[int] = field(default_factory=set)
    unknown: set[str] = field(default_factory=set)


def media_for_arr(service: str, instance_id: int | None, arr_id: int) -> int | None:
    """Média suivi par cette fiche Sonarr/Radarr (`instance_id` None =
    instance principale)."""
    column = col(Media.sonarr_id) if service == "sonarr" else col(Media.radarr_id)
    instance = col(Media.arr_instance_id)
    with Session(engine) as session:
        query = select(Media.id).where(
            column == arr_id, instance.is_(None) if instance_id is None else instance == instance_id
        )
        return session.exec(query).first()


def media_for_hashes(hashes: Iterable[str]) -> Resolved:
    wanted = {h.lower() for h in hashes if h}
    resolved = Resolved()
    if not wanted:
        return resolved
    with Session(engine) as session:
        # Hashes stockés tels que le client les donne : comparaison sans casse.
        rows = session.exec(
            select(Torrent.hash, Torrent.media_id).where(func.lower(col(Torrent.hash)).in_(list(wanted)))
        ).all()
    resolved.media_ids = {media_id for _, media_id in rows if media_id is not None}
    # Un torrent connu mais rattaché à aucun média reste « inconnu » : seule
    # une analyse du client torrent peut le rattacher.
    resolved.unknown = wanted - {h.lower() for h, media_id in rows if media_id is not None}
    return resolved


def media_for_items(item_ids: Iterable[str]) -> Resolved:
    """Éléments du serveur multimédia : un film ou une série (l'épisode est
    ramené à sa série par l'appelant)."""
    wanted = {i for i in item_ids if i}
    resolved = Resolved()
    if not wanted:
        return resolved
    with Session(engine) as session:
        rows = session.exec(select(Media.id, Media.emby_item_id).where(col(Media.emby_item_id).in_(list(wanted)))).all()
    resolved.media_ids = {media_id for media_id, _ in rows if media_id is not None}
    resolved.unknown = wanted - {item_id for _, item_id in rows if item_id}
    return resolved
