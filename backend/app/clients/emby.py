from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from app.models.settings import Settings


class EmbyClient:
    """Client du serveur multimédia : Emby, ou Jellyfin (fork d'Emby, API quasi
    identique). Les seules différences sont gérées ici, vérifiées contre la
    spécification OpenAPI de Jellyfin 10.11 et son code d'authentification :

    - Authentification : Jellyfin n'accepte `X-Emby-Token` que si
      « l'autorisation héritée » est activée (désactivée par défaut) — en-tête
      `Authorization: MediaBrowser Token="…"` à la place.
    - Éléments vus par un utilisateur : `/Users/{id}/Items` n'existe plus côté
      Jellyfin, remplacé par `/Items?userId=`.
    - Avatar d'un utilisateur : `/UserImage?userId=` côté Jellyfin.
    - Paramètre `Fields` : Jellyfin le valide contre l'énumération `ItemFields`
      et renvoie 400 sur une valeur inconnue, alors qu'Emby ignore ce qu'il ne
      connaît pas. `IndexNumber`, `ParentIndexNumber`, `IndexNumberEnd` et
      `ImageTags` n'en font PAS partie : ce sont des propriétés renvoyées
      d'office sur chaque élément. Elles ne sont donc demandées qu'à Emby
      (`_fields`), jamais à Jellyfin.

    Tout le reste (champs `UserData`, `ProviderIds`, `MediaSources`, filtres
    `IsPlayed`/`IsResumable`, `Policy.IsDisabled`...) est identique."""

    def __init__(self, base_url: str, api_key: str, server_type: str = "emby"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.is_jellyfin = server_type == "jellyfin"

    # Valeurs acceptées par les deux serveurs (sous-ensemble de l'énumération
    # ItemFields de Jellyfin). Tout le reste n'est envoyé qu'à Emby.
    _SHARED_FIELDS = frozenset({"ProviderIds", "Path", "MediaSources", "DateCreated"})

    def _fields(self, *names: str) -> str:
        """Filtre les champs qu'un serveur refuserait (voir docstring)."""
        if self.is_jellyfin:
            names = tuple(n for n in names if n in self._SHARED_FIELDS)
        return ",".join(names)

    def auth_headers(self) -> dict[str, str]:
        if self.is_jellyfin:
            return {"Authorization": f'MediaBrowser Token="{self.api_key}"'}
        return {"X-Emby-Token": self.api_key}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, headers=self.auth_headers(), timeout=30.0)

    async def get_library_items(self, item_types: str) -> list[dict[str, Any]]:
        async with self._client() as client:
            resp = await client.get(
                "/Items",
                params={
                    "Recursive": "true",
                    "IncludeItemTypes": item_types,
                    "Fields": self._fields("ProviderIds", "Path", "MediaSources", "ImageTags", "DateCreated"),
                },
            )
            resp.raise_for_status()
            return resp.json().get("Items", [])

    async def get_items_by_ids(self, item_ids: list[str]) -> list[dict[str, Any]]:
        """Éléments précis, par identifiant — pour rafraîchir un seul média
        sans relire toute la bibliothèque."""
        if not item_ids:
            return []
        async with self._client() as client:
            resp = await client.get(
                "/Items",
                params={
                    "Recursive": "true",
                    "Ids": ",".join(item_ids),
                    "Fields": self._fields("ProviderIds", "Path", "MediaSources", "ImageTags", "DateCreated"),
                },
            )
            resp.raise_for_status()
            return resp.json().get("Items", [])

    async def get_episodes(self, series_item_id: str) -> list[dict[str, Any]]:
        async with self._client() as client:
            resp = await client.get(
                "/Items",
                params={
                    "ParentId": series_item_id,
                    "IncludeItemTypes": "Episode",
                    "Recursive": "true",
                    # IndexNumberEnd : dernier épisode couvert par un fichier
                    # multi-épisodes (S03E01-E02 fusionnés en un seul item).
                    "Fields": self._fields(
                        "Path", "MediaSources", "IndexNumber", "ParentIndexNumber", "IndexNumberEnd"
                    ),
                },
            )
            resp.raise_for_status()
            return resp.json().get("Items", [])

    async def get_users(self) -> list[dict[str, Any]]:
        async with self._client() as client:
            resp = await client.get("/Users")
            resp.raise_for_status()
            return resp.json()

    async def get_user_items(
        self,
        user_id: str,
        item_type: str,
        *,
        ids: str | None = None,
        parent_id: str | None = None,
        filters: str | None = None,
    ) -> list[dict[str, Any]]:
        """Éléments visibles par CET utilisateur (bibliothèques auxquelles il a
        accès), avec son état de lecture (`UserData`). Sans `ids`/`parent_id` :
        toute la bibliothèque, pour le scan ; avec : un seul média, pour
        rafraîchir une fiche."""
        params = {
            "Recursive": "true",
            "IncludeItemTypes": item_type,
            "EnableUserData": "true",
            "EnableImages": "false",
        }
        if ids:
            params["Ids"] = ids
        if parent_id:
            params["ParentId"] = parent_id
        if filters:
            params["Filters"] = filters
        if self.is_jellyfin:
            path = "/Items"
            params["userId"] = user_id
        else:
            path = f"/Users/{quote(user_id, safe='')}/Items"
        async with self._client() as client:
            resp = await client.get(path, params=params)
            resp.raise_for_status()
            return resp.json().get("Items", [])

    async def _fetch_image(self, path: str, params: dict[str, str] | None = None) -> tuple[bytes, str] | None:
        async with self._client() as client:
            resp = await client.get(path, params=params)
            if resp.status_code != 200:
                return None
            return resp.content, resp.headers.get("content-type", "image/jpeg")

    async def fetch_poster(self, item_id: str) -> tuple[bytes, str] | None:
        return await self._fetch_image(f"/Items/{quote(item_id, safe='')}/Images/Primary")

    async def fetch_user_avatar(self, user_id: str) -> tuple[bytes, str] | None:
        if self.is_jellyfin:
            return await self._fetch_image("/UserImage", params={"userId": user_id})
        return await self._fetch_image(f"/Users/{quote(user_id, safe='')}/Images/Primary")


def media_server_client(settings: "Settings | None") -> EmbyClient | None:
    """Client du serveur multimédia configuré, ou None s'il ne l'est pas."""
    if settings is None or not settings.emby_url or not settings.emby_api_key:
        return None
    return EmbyClient(settings.emby_url, settings.emby_api_key, settings.media_server)


def media_server_name(settings: "Settings | None") -> str:
    return "Jellyfin" if settings is not None and settings.media_server == "jellyfin" else "Emby"
