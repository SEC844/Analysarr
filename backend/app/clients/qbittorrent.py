from typing import Any

import httpx


class QbittorrentAuthError(Exception):
    pass


class QbittorrentClient:
    """Client qBittorrent Web API v2, authentifié par cookie de session.

    Le succès de la connexion se juge sur la présence d'un cookie *SID* plutôt
    que sur le corps de la réponse : les versions récentes de qBittorrent
    renvoient 204 No Content au lieu de l'historique 200 "Ok." (voir
    app/services/connection_test.py pour le même constat sur le testeur de
    connexion des Réglages).
    """

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "QbittorrentClient":
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        resp = await self._client.post(
            "/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
            headers={"Referer": self.base_url, "Origin": self.base_url},
        )
        has_session_cookie = any("sid" in name.lower() for name in resp.cookies)
        if not (has_session_cookie or (resp.status_code == 200 and resp.text.strip() == "Ok.")):
            await self._client.aclose()
            self._client = None
            raise QbittorrentAuthError("Authentification qBittorrent refusée.")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("QbittorrentClient doit être utilisé via 'async with'.")
        return self._client

    async def get_torrents(self) -> list[dict[str, Any]]:
        resp = await self.client.get("/api/v2/torrents/info")
        resp.raise_for_status()
        return resp.json()

    async def get_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        resp = await self.client.get("/api/v2/torrents/trackers", params={"hash": torrent_hash})
        resp.raise_for_status()
        return resp.json()

    async def get_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        """Liste des fichiers du torrent, avec leur chemin relatif à `save_path`.
        Indispensable pour les torrents multi-fichiers (pack saison, intégrale) :
        `content_path` n'y désigne que le dossier racine, pas un fichier précis."""
        resp = await self.client.get("/api/v2/torrents/files", params={"hash": torrent_hash})
        resp.raise_for_status()
        return resp.json()

    async def delete_torrents(self, hashes: list[str], delete_files: bool) -> None:
        if not hashes:
            return
        resp = await self.client.post(
            "/api/v2/torrents/delete",
            data={"hashes": "|".join(hashes), "deleteFiles": str(delete_files).lower()},
        )
        resp.raise_for_status()
