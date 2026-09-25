from typing import Any

import httpx


class OmbiClient:
    """Client API Ombi (v4), alternative à Seer, authentifié par la clé API
    des réglages (en-tête `ApiKey`, vu comme un administrateur).

    Vérifié dans le code source d'Ombi (`RequestController`, `ApiKeyMiddlewear`,
    sérialisation Newtonsoft en camelCase) : `/api/v1/Request/movie` et
    `/api/v1/Request/tv` renvoient TOUTES les demandes d'un coup, sans
    pagination, avec leur demandeur (`requestedUser`) — et, pour les séries,
    une demande enfant par utilisateur avec ses saisons."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, headers={"ApiKey": self.api_key}, timeout=30.0)

    async def _get_list(self, path: str) -> list[dict[str, Any]]:
        async with self._client() as client:
            resp = await client.get(path)
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, list):
            raise ValueError(f"Réponse inattendue d'Ombi pour {path}")
        return [item for item in data if isinstance(item, dict)]

    async def get_movie_requests(self) -> list[dict[str, Any]]:
        return await self._get_list("/api/v1/Request/movie")

    async def get_tv_requests(self) -> list[dict[str, Any]]:
        return await self._get_list("/api/v1/Request/tv")
