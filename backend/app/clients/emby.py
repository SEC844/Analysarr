from typing import Any

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
                    "Fields": "ProviderIds,Path,MediaSources",
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

    def poster_url(self, item_id: str) -> str:
        return f"{self.base_url}/Items/{item_id}/Images/Primary?api_key={self.api_key}"

    async def fetch_poster(self, item_id: str) -> tuple[bytes, str] | None:
        async with self._client() as client:
            resp = await client.get(f"/Items/{item_id}/Images/Primary")
            if resp.status_code != 200:
                return None
            return resp.content, resp.headers.get("content-type", "image/jpeg")
