from typing import Any

import httpx

# Garde-fou de pagination : jamais de boucle infinie sur une réponse incohérente.
_MAX_REQUESTS = 50_000
_PAGE_SIZE = 100


class SeerClient:
    """Client API Seer (Overseerr, Jellyseerr, Seerr : même API v1), authentifié
    par clé API (`X-Api-Key`, compte administrateur)."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, headers={"X-Api-Key": self.api_key}, timeout=30.0)

    async def get_requests(self) -> list[dict[str, Any]]:
        requests: list[dict[str, Any]] = []
        async with self._client() as client:
            skip = 0
            while skip < _MAX_REQUESTS:
                resp = await client.get(
                    "/api/v1/request",
                    params={"take": _PAGE_SIZE, "skip": skip, "filter": "all", "sort": "added"},
                )
                resp.raise_for_status()
                data = resp.json()
                page = data.get("results") or []
                requests.extend(page)
                skip += _PAGE_SIZE
                total = (data.get("pageInfo") or {}).get("results")
                if len(page) < _PAGE_SIZE or (isinstance(total, int) and skip >= total):
                    break
        return requests
