from typing import Any

import httpx


class ArrClient:
    """Base commune Sonarr/Radarr : même schéma d'authentification (X-Api-Key) et d'API v3."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, headers={"X-Api-Key": self.api_key}, timeout=30.0)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with self._client() as client:
            resp = await client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()

    async def _delete(self, path: str) -> None:
        async with self._client() as client:
            resp = await client.delete(path)
            resp.raise_for_status()


class RadarrClient(ArrClient):
    async def get_movies(self) -> list[dict[str, Any]]:
        return await self._get("/api/v3/movie")

    async def get_history_for_movie(self, movie_id: int) -> list[dict[str, Any]]:
        return await self._get("/api/v3/history/movie", params={"movieId": movie_id})

    async def delete_movie_file(self, file_id: int) -> None:
        await self._delete(f"/api/v3/moviefile/{file_id}")


class SonarrClient(ArrClient):
    async def get_series(self) -> list[dict[str, Any]]:
        return await self._get("/api/v3/series")

    async def get_episode_files(self, series_id: int) -> list[dict[str, Any]]:
        return await self._get("/api/v3/episodefile", params={"seriesId": series_id})

    async def get_episodes(self, series_id: int) -> list[dict[str, Any]]:
        return await self._get("/api/v3/episode", params={"seriesId": series_id})

    async def get_history_for_series(self, series_id: int) -> list[dict[str, Any]]:
        # /api/v3/history?seriesId=X n'existe pas : ce paramètre n'est pas
        # reconnu par l'endpoint paginé générique (qui attend `seriesIds`, un
        # tableau) et Sonarr l'ignore silencieusement plutôt que de renvoyer une
        # erreur — l'appel réussit mais renvoie l'historique global le plus
        # récent, TOUTES séries confondues. Une précédente version de ce client
        # avait ce bug : chaque torrent d'une autre série présent dans les 250
        # événements les plus récents se retrouvait attribué à la série en cours
        # de traitement. Le bon endpoint dédié est /api/v3/history/series (comme
        # /api/v3/history/movie pour Radarr), qui renvoie une liste brute déjà
        # filtrée par série.
        return await self._get("/api/v3/history/series", params={"seriesId": series_id})

    async def delete_episode_file(self, file_id: int) -> None:
        await self._delete(f"/api/v3/episodefile/{file_id}")
