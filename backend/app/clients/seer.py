from typing import Any

import httpx

# Garde-fou de pagination : jamais de boucle infinie sur une réponse incohérente.
_MAX_REQUESTS = 50_000
_PAGE_SIZE = 100


class SeerRequestError(RuntimeError):
    """Seer a refusé la demande : message lisible plutôt qu'un code HTTP seul."""


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

    async def get_request(self, request_id: int) -> dict[str, Any] | None:
        """Demande complète, capturée AVANT suppression pour pouvoir la
        recréer depuis la corbeille. `None` si Seer ne la connaît plus."""
        async with self._client() as client:
            resp = await client.get(f"/api/v1/request/{int(request_id)}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def create_request(self, body: dict[str, Any]) -> dict[str, Any]:
        """Recrée une demande au nom de son demandeur d'origine (`userId`,
        réservé aux comptes administrateurs — c'est déjà ce que la clé API
        exige côté Analysarr).

        Seer répond 500 sur une demande qu'il juge invalide sans rien dire de
        plus dans le code HTTP : le corps de la réponse est donc remonté
        (tronqué), sinon l'utilisateur ne voit qu'un « 500 » opaque."""
        async with self._client() as client:
            resp = await client.post("/api/v1/request", json=body)
            if resp.is_error:
                detail = " ".join((resp.text or "").split())[:200]
                raise SeerRequestError(f"HTTP {resp.status_code}{' — ' + detail if detail else ''}")
            return resp.json()

    async def _delete(self, path: str) -> None:
        async with self._client() as client:
            resp = await client.delete(path)
            # Déjà absent côté Seer : l'objectif (plus de demande) est atteint.
            if resp.status_code != 404:
                resp.raise_for_status()

    async def delete_media(self, media_id: int) -> None:
        """Supprime la fiche du média et ses demandes : il redevient demandable."""
        await self._delete(f"/api/v1/media/{int(media_id)}")

    async def delete_request(self, request_id: int) -> None:
        await self._delete(f"/api/v1/request/{int(request_id)}")
