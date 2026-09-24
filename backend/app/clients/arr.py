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

    async def _post(self, path: str, json: Any) -> Any:
        async with self._client() as client:
            resp = await client.post(path, json=json)
            resp.raise_for_status()
            return resp.json() if resp.content else None

    async def get_root_folders(self) -> list[dict[str, Any]]:
        """Dossiers racine déclarés dans Sonarr/Radarr. Un ajout ne peut viser
        qu'un de ces dossiers : c'est la liste qui borne le chemin accepté."""
        return await self._get("/api/v3/rootfolder")

    async def get_quality_profiles(self) -> list[dict[str, Any]]:
        return await self._get("/api/v3/qualityprofile")

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

    async def find_movie(self, tmdb_id: int | None, imdb_id: str | None) -> dict[str, Any] | None:
        """Film DÉJÀ suivi par cette instance, retrouvé par identifiant externe.
        Sert après un rattachement : la fiche Analysarr doit adopter l'identité
        Radarr sans attendre le prochain scan complet."""
        for params in ({"tmdbId": tmdb_id} if tmdb_id else None, {"imdbId": imdb_id} if imdb_id else None):
            if params is None:
                continue
            try:
                found = await self._get("/api/v3/movie", params=params)
            except httpx.HTTPStatusError:
                continue
            for movie in found or []:
                if isinstance(movie, dict) and movie.get("id"):
                    return movie
        return None

    async def get_history_for_movie(self, movie_id: int) -> list[dict[str, Any]]:
        return await self._get("/api/v3/history/movie", params={"movieId": movie_id})

    async def delete_movie_file(self, file_id: int) -> None:
        await self._delete(f"/api/v3/moviefile/{file_id}")

    async def rescan_movie(self, movie_id: int) -> None:
        """Relit le dossier du film. Réservé à l'annulation d'une suppression :
        les fichiers remis en place redeviennent connus de Radarr. Jamais après
        un ajout — Radarr rafraîchit déjà ce qu'il ajoute, et un second scan en
        parallèle enregistrait deux fois fichiers et NFO (bug réel)."""
        await self._post("/api/v3/command", {"name": "RescanMovie", "movieId": movie_id})

    async def delete_movie(self, movie_id: int, delete_files: bool = True) -> None:
        """Retire le film de Radarr (arrête le suivi/monitoring) et, par
        défaut, supprime son fichier — équivalent de "Supprimer" depuis l'UI
        Radarr elle-même. `addImportExclusion=false` : on ne bloque pas un
        futur ré-ajout volontaire du film, on arrête juste de le suivre
        maintenant.

        `delete_files=False` quand la corbeille est active : c'est Analysarr
        qui met alors les fichiers de côté, sinon Radarr les effacerait pour de
        bon et il n'y aurait plus rien à restaurer."""
        await self._delete(
            f"/api/v3/movie/{movie_id}",
            params={"deleteFiles": str(delete_files).lower(), "addImportExclusion": "false"},
        )


    async def lookup_movie_by_tmdb(self, tmdb_id: int) -> dict[str, Any] | None:
        """Fiche Radarr d'un film par identifiant TMDB (endpoint dédié : pas de
        recherche texte, donc pas d'homonyme)."""
        try:
            movie = await self._get("/api/v3/movie/lookup/tmdb", params={"tmdbId": tmdb_id})
        except httpx.HTTPStatusError:
            return None
        return movie if isinstance(movie, dict) and movie.get("tmdbId") else None

    async def lookup_movie_by_imdb(self, imdb_id: str) -> dict[str, Any] | None:
        try:
            movie = await self._get("/api/v3/movie/lookup/imdb", params={"imdbId": imdb_id})
        except httpx.HTTPStatusError:
            return None
        return movie if isinstance(movie, dict) and movie.get("tmdbId") else None

    async def lookup_movies(self, term: str) -> list[dict[str, Any]]:
        """Recherche par titre : sert quand aucun identifiant n'est exploitable
        (ou faux), et ses résultats sont toujours confrontés au titre et à
        l'année du média avant d'être proposés."""
        try:
            results = await self._get("/api/v3/movie/lookup", params={"term": term})
        except httpx.HTTPStatusError:
            return []
        return [movie for movie in results or [] if isinstance(movie, dict)]

    async def add_movie(self, body: dict[str, Any]) -> dict[str, Any]:
        """Ajoute un film : restauration depuis la corbeille (fiche capturée
        avant suppression) ou rattachement d'un média de la bibliothèque (fiche
        de `movie/lookup`). `id` et les champs de fichier sont retirés — Radarr
        en attribue de nouveaux et redécouvre les fichiers au rescan. Les
        options d'ajout (`addOptions`, `path`, profil) viennent de l'appelant,
        qui sait s'il ré-ajoute ou s'il importe un dossier existant."""
        payload = {key: value for key, value in body.items() if key not in ("id", "movieFile", "movieFileId")}
        payload.setdefault("addOptions", {"searchForMovie": False})
        return await self._post("/api/v3/movie", payload)

    async def import_movies(self, movies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Import en masse de dossiers déjà présents sur le disque : c'est
        exactement ce que fait « Importer N films » dans Radarr (écran
        `/add/import`). Radarr ajoute chaque film PUIS rafraîchit sa fiche, ce
        qui déclenche le scan du dossier et l'import du fichier existant."""
        created = await self._post("/api/v3/movie/import", movies)
        return created if isinstance(created, list) else []


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

    async def find_series(self, tvdb_id: int | None) -> dict[str, Any] | None:
        """Série DÉJÀ suivie par cette instance (voir `RadarrClient.find_movie`).
        Sonarr ne filtre que par identifiant TVDB."""
        if not tvdb_id:
            return None
        try:
            found = await self._get("/api/v3/series", params={"tvdbId": tvdb_id})
        except httpx.HTTPStatusError:
            return None
        for series in found or []:
            if isinstance(series, dict) and series.get("id"):
                return series
        return None

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

    async def delete_series(self, series_id: int, delete_files: bool = True) -> None:
        """Retire la série de Sonarr et, par défaut, supprime son dossier —
        équivalent de "Supprimer" depuis l'UI Sonarr, seul cas où la
        granularité série s'applique (tous les fichiers de la série
        sélectionnés). Même choix que `RadarrClient.delete_movie` : pas
        d'exclusion d'import, et `delete_files=False` quand la corbeille est
        active."""
        await self._delete(
            f"/api/v3/series/{series_id}",
            params={"deleteFiles": str(delete_files).lower(), "addImportListExclusion": "false"},
        )

    async def lookup_series(self, term: str) -> list[dict[str, Any]]:
        """Recherche de séries. Sonarr ne comprend que le préfixe `tvdb:`
        (vérifié dans SkyHookProxy.SearchForNewSeries) : un `imdb:`/`tmdb:`
        retomberait en recherche texte, donc l'appelant passe l'identifiant
        TVDB quand il l'a, et le titre sinon."""
        try:
            results = await self._get("/api/v3/series/lookup", params={"term": term})
        except httpx.HTTPStatusError:
            return []
        return [series for series in results or [] if isinstance(series, dict)]

    async def add_series(self, body: dict[str, Any]) -> dict[str, Any]:
        """Ajoute une série (voir `RadarrClient.add_movie`) : restauration ou
        rattachement d'un dossier existant, selon les `addOptions` fournies."""
        payload = {key: value for key, value in body.items() if key not in ("id", "episodeFileCount", "statistics")}
        payload.setdefault(
            "addOptions",
            {"searchForMissingEpisodes": False, "searchForCutoffUnmetEpisodes": False, "monitor": "none"},
        )
        return await self._post("/api/v3/series", payload)

    async def import_series(self, series: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Équivalent Sonarr de `RadarrClient.import_movies` (écran
        « Import Existing Series »)."""
        created = await self._post("/api/v3/series/import", series)
        return created if isinstance(created, list) else []

    async def delete_episode_file(self, file_id: int) -> None:
        await self._delete(f"/api/v3/episodefile/{file_id}")

    async def rescan_series(self, series_id: int) -> None:
        """Voir `RadarrClient.rescan_movie` : réservé à l'annulation d'une
        suppression."""
        await self._post("/api/v3/command", {"name": "RescanSeries", "seriesId": series_id})

    async def set_episodes_monitored(self, episode_ids: list[int], monitored: bool) -> None:
        """Sonarr n'a pas d'équivalent "supprimer cet épisode" comme Radarr
        pour un film : la granularité de suivi est l'épisode, via ce
        endpoint de (dé)monitoring en masse. Démonitorer un épisode dont le
        fichier vient d'être supprimé empêche Sonarr de le re-télécharger
        automatiquement — c'est ça, "supprimer de Sonarr" à cette échelle."""
        if not episode_ids:
            return
        await self._put("/api/v3/episode/monitor", json={"episodeIds": episode_ids, "monitored": monitored})
