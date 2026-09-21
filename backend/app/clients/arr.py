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

    async def _delete(self, path: str, params: dict[str, Any] | None = None) -> None:
        async with self._client() as client:
            resp = await client.delete(path, params=params)
            resp.raise_for_status()

    async def _put(self, path: str, json: dict[str, Any]) -> None:
        async with self._client() as client:
            resp = await client.put(path, json=json)
            resp.raise_for_status()

    async def _post(self, path: str, json: dict[str, Any]) -> Any:
        async with self._client() as client:
            resp = await client.post(path, json=json)
            resp.raise_for_status()
            return resp.json() if resp.content else None

    async def _queue(self, extra_params: dict[str, Any]) -> list[dict[str, Any]]:
        """File d'attente paginée. Sonarr et Radarr renvoient au maximum une
        page à la fois ; le garde-fou à 25 pages évite une boucle infinie si un
        serveur renvoie toujours la même page."""
        records: list[dict[str, Any]] = []
        for page in range(1, 26):
            params = {"page": page, "pageSize": 200, **extra_params}
            data = await self._get("/api/v3/queue", params=params)
            batch = data.get("records") if isinstance(data, dict) else data
            if not batch:
                break
            records.extend(batch)
            total = data.get("totalRecords") if isinstance(data, dict) else None
            if total is not None and len(records) >= total:
                break
        return records

    async def manual_import_candidates(
        self, *, download_id: str | None = None, folder: str | None = None
    ) -> list[dict[str, Any]]:
        """Fichiers proposés à l'import, avec les motifs de refus éventuels
        (`rejections`). Par téléchargement, ou à défaut par dossier de sortie —
        Sonarr/Radarr ne connaît plus le `downloadId` dès que l'entrée a quitté
        la file d'attente, alors que le dossier, lui, existe toujours.

        `filterExistingFiles=false` : on veut TOUS les fichiers du
        téléchargement, y compris ceux que Sonarr/Radarr écarterait, pour
        pouvoir afficher la raison plutôt qu'une liste vide."""
        params: dict[str, Any] = {"filterExistingFiles": "false"}
        if download_id:
            params["downloadId"] = download_id
        if folder:
            params["folder"] = folder
        return await self._get("/api/v3/manualimport", params=params)

    async def process_monitored_downloads(self) -> None:
        """Demande à Sonarr/Radarr de repasser sur sa file d'attente et de
        retenter les imports en attente — exactement ce que fait la tâche
        planifiée du même nom. Dernier recours quand aucun fichier n'est
        proposé à l'import."""
        await self._post("/api/v3/command", json={"name": "ProcessMonitoredDownloads"})

    async def manual_import(self, files: list[dict[str, Any]]) -> None:
        """Relance l'import des fichiers choisis (équivalent du bouton
        « Manual Import » de Sonarr/Radarr). `importMode: auto` laisse le
        serveur décider entre déplacement et copie selon sa configuration —
        indispensable pour ne pas casser les hardlinks d'un torrent en seed."""
        if not files:
            return
        await self._post("/api/v3/command", json={"name": "ManualImport", "importMode": "auto", "files": files})


class RadarrClient(ArrClient):
    async def get_movies(self) -> list[dict[str, Any]]:
        return await self._get("/api/v3/movie")

    async def get_queue(self) -> list[dict[str, Any]]:
        return await self._queue({"includeUnknownMovieItems": "false", "includeMovie": "false"})

    async def get_movie(self, movie_id: int) -> dict[str, Any] | None:
        """Un seul film, pour l'analyse d'un média. `None` si Radarr ne le
        suit plus (404) : le média a été retiré depuis le dernier scan."""
        try:
            return await self._get(f"/api/v3/movie/{movie_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def get_history_for_movie(self, movie_id: int) -> list[dict[str, Any]]:
        return await self._get("/api/v3/history/movie", params={"movieId": movie_id})

    async def delete_movie_file(self, file_id: int) -> None:
        await self._delete(f"/api/v3/moviefile/{file_id}")

    async def delete_movie(self, movie_id: int) -> None:
        """Retire le film de Radarr (arrête le suivi/monitoring) ET supprime
        son fichier — équivalent de "Supprimer" depuis l'UI Radarr elle-même.
        `addImportExclusion=false` : on ne bloque pas un futur ré-ajout
        volontaire du film, on arrête juste de le suivre maintenant."""
        await self._delete(f"/api/v3/movie/{movie_id}", params={"deleteFiles": "true", "addImportExclusion": "false"})


class SonarrClient(ArrClient):
    async def get_series(self) -> list[dict[str, Any]]:
        return await self._get("/api/v3/series")

    async def get_queue(self) -> list[dict[str, Any]]:
        return await self._queue({"includeUnknownSeriesItems": "false", "includeSeries": "false"})

    async def get_series_by_id(self, series_id: int) -> dict[str, Any] | None:
        """Une seule série (voir `RadarrClient.get_movie`)."""
        try:
            return await self._get(f"/api/v3/series/{series_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

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

    async def delete_series(self, series_id: int) -> None:
        """Retire la série de Sonarr ET supprime son dossier — équivalent de
        "Supprimer" depuis l'UI Sonarr, seul cas où la granularité série
        s'applique (tous les fichiers de la série sélectionnés). Même choix
        que `RadarrClient.delete_movie` : pas d'exclusion d'import."""
        await self._delete(
            f"/api/v3/series/{series_id}", params={"deleteFiles": "true", "addImportListExclusion": "false"}
        )

    async def delete_episode_file(self, file_id: int) -> None:
        await self._delete(f"/api/v3/episodefile/{file_id}")

    async def set_episodes_monitored(self, episode_ids: list[int], monitored: bool) -> None:
        """Sonarr n'a pas d'équivalent "supprimer cet épisode" comme Radarr
        pour un film : la granularité de suivi est l'épisode, via ce
        endpoint de (dé)monitoring en masse. Démonitorer un épisode dont le
        fichier vient d'être supprimé empêche Sonarr de le re-télécharger
        automatiquement — c'est ça, "supprimer de Sonarr" à cette échelle."""
        if not episode_ids:
            return
        await self._put("/api/v3/episode/monitor", json={"episodeIds": episode_ids, "monitored": monitored})
