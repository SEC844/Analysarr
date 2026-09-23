import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlmodel import Session, select

from app.clients.emby import media_server_client
from app.database import get_session
from app.models.media import EmbyUser
from app.models.settings import Settings
from app.schemas.media import EmbyUserRead
from app.services.poster_cache import read_cached_poster, safe_image_type, write_cached_poster
from app.services.watch_stats import users_from_api

router = APIRouter()

# Identifiants Emby/Jellyfin : hexadécimal (GUID, tirets éventuels). Validé
# avant tout usage (appel au serveur multimédia, nom de fichier du cache).
_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_emby = media_server_client


@router.get("/users", response_model=list[EmbyUserRead])
async def list_emby_users(session: Session = Depends(get_session)) -> list[EmbyUserRead]:
    """Utilisateurs Emby, en direct (repli sur le dernier scan si Emby est
    injoignable) — pour choisir ceux exclus des statistiques de visionnage."""
    emby = _emby(session.get(Settings, 1))
    users: list[EmbyUser] | None = None
    if emby is not None:
        try:
            users = users_from_api(await emby.get_users())
        except (httpx.HTTPError, ValueError):
            users = None
    if users is None:
        users = list(session.exec(select(EmbyUser)).all())
    return [
        EmbyUserRead(id=u.id, name=u.name, image_tag=u.image_tag, is_disabled=u.is_disabled)
        for u in sorted(users, key=lambda u: u.name.lower())
    ]


@router.get("/users/{user_id}/avatar")
async def get_user_avatar(user_id: str, session: Session = Depends(get_session)) -> Response:
    if not _USER_ID_PATTERN.match(user_id):
        raise HTTPException(404, "Avatar introuvable.")
    user = session.get(EmbyUser, user_id)
    if user is None or not user.image_tag:
        raise HTTPException(404, "Avatar introuvable.")

    cache_key = f"user_{user.id}"
    cached = read_cached_poster(cache_key, user.image_tag)
    if cached is not None:
        content, content_type = cached
    else:
        emby = _emby(session.get(Settings, 1))
        result = await emby.fetch_user_avatar(user.id) if emby else None
        image_type = safe_image_type(result[1]) if result else None
        if result is None or image_type is None:
            raise HTTPException(404, "Avatar introuvable.")
        content, content_type = result[0], image_type
        write_cached_poster(cache_key, user.image_tag, content, content_type)

    # URL propre à la version de l'avatar (`?v=<tag>` côté frontend) : mise en
    # cache navigateur indéfinie sans risque de contenu périmé.
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )
