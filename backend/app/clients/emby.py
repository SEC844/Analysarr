from typing import Any
from urllib.parse import quote

import httpx


class EmbyClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, headers={"X-Emby-Token": self.api_key}, timeout=30.0)

    async def get_library_items(self, item_types: str) -> list[dict[str, Any]]:
        async with self._client() as client:
            resp = await client.get(
                "/Items",
                params={
                    "Recursive": "true",
                    "IncludeItemTypes": item_types,
                    "Fields": "ProviderIds,Path,MediaSources,ImageTags,DateCreated",
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
                    "Fields": "Path,MediaSources,IndexNumber,ParentIndexNumber",
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
        async with self._client() as client:
            resp = await client.get(f"/Users/{quote(user_id, safe='')}/Items", params=params)
            resp.raise_for_status()
            return resp.json().get("Items", [])

    async def _fetch_image(self, path: str) -> tuple[bytes, str] | None:
        async with self._client() as client:
            resp = await client.get(path)
            if resp.status_code != 200:
                return None
            return resp.content, resp.headers.get("content-type", "image/jpeg")

    async def fetch_poster(self, item_id: str) -> tuple[bytes, str] | None:
        return await self._fetch_image(f"/Items/{quote(item_id, safe='')}/Images/Primary")

    async def fetch_user_avatar(self, user_id: str) -> tuple[bytes, str] | None:
        return await self._fetch_image(f"/Users/{quote(user_id, safe='')}/Images/Primary")
