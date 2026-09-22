"""Client Deluge (interface Web, port 8112 par défaut) : JSON-RPC sur `/json`,
session par cookie.

Deluge sépare le Web UI du démon : après l'authentification, le Web UI doit
être connecté à un démon (`web.connected`), sinon toutes les méthodes `core.*`
échouent. Un seul appel `core.get_torrents_status` ramène statuts, fichiers et
trackers de TOUS les torrents — ils sont ensuite servis depuis ce cache pour ne
pas multiplier les requêtes pendant un scan."""

import base64
from typing import Any

import httpx

from app.clients.torrent_base import TorrentAuthError, TorrentClient, content_path_for

# `download_location` est le nom Deluge 2 ; `save_path` celui de Deluge 1.
STATUS_FIELDS = [
    "name",
    "download_location",
    "save_path",
    "total_size",
    "ratio",
    "num_seeds",
    "num_peers",
    "time_added",
    "completed_time",
    "label",
    "files",
    "trackers",
]


class DelugeClient(TorrentClient):
    name = "Deluge"

    def __init__(self, base_url: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.password = password
        self._client: httpx.AsyncClient | None = None
        self._request_id = 0
        self._torrents: dict[str, dict[str, Any]] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("DelugeClient doit être utilisé via 'async with'.")
        return self._client

    async def __aenter__(self) -> "DelugeClient":
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)
        try:
            if not await self._rpc("auth.login", [self.password]):
                raise TorrentAuthError("Mot de passe Deluge refusé.")
            if not await self._rpc("web.connected", []):
                hosts = await self._rpc("web.get_hosts", []) or []
                if not hosts:
                    raise TorrentAuthError("Aucun démon Deluge déclaré dans l'interface web.")
                await self._rpc("web.connect", [hosts[0][0]])
        except BaseException:
            await self.__aexit__()
            raise
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        self._request_id += 1
        resp = await self.client.post("/json", json={"method": method, "params": params, "id": self._request_id})
        if resp.status_code in (401, 403):
            raise TorrentAuthError(f"Accès refusé par Deluge ({resp.status_code}).")
        resp.raise_for_status()
        payload = resp.json()
        error = payload.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            # Code 1 : session expirée ou absente (Deluge ne renvoie pas de 401).
            if isinstance(error, dict) and error.get("code") == 1:
                raise TorrentAuthError(f"Session Deluge refusée : {message}")
            raise RuntimeError(f"Deluge a refusé {method} : {message}")
        return payload.get("result")

    def _normalize(self, torrent_hash: str, status: dict[str, Any]) -> dict[str, Any]:
        save_path = status.get("download_location") or status.get("save_path")
        paths = [f.get("path") for f in status.get("files") or [] if f.get("path")]
        return {
            "hash": torrent_hash,
            "name": status.get("name", ""),
            "save_path": save_path,
            "content_path": content_path_for(save_path, paths),
            "category": status.get("label") or None,
            "size": status.get("total_size"),
            "ratio": status.get("ratio"),
            "num_seeds": status.get("num_seeds"),
            "num_leechs": status.get("num_peers"),
            "added_on": status.get("time_added"),
            "completion_on": status.get("completed_time"),
        }

    async def get_torrents(self) -> list[dict[str, Any]]:
        statuses = await self._rpc("core.get_torrents_status", [{}, STATUS_FIELDS]) or {}
        self._torrents = statuses
        return [self._normalize(torrent_hash, status) for torrent_hash, status in statuses.items()]

    async def _status(self, torrent_hash: str) -> dict[str, Any]:
        cached = self._torrents.get(torrent_hash)
        if cached is not None:
            return cached
        return await self._rpc("core.get_torrent_status", [torrent_hash, STATUS_FIELDS]) or {}

    async def get_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        status = await self._status(torrent_hash)
        # Deluge ne publie pas d'état par tracker, seulement un message global.
        return [{"url": tracker.get("url", ""), "status": None} for tracker in status.get("trackers") or []]

    async def get_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        status = await self._status(torrent_hash)
        return [{"name": f.get("path"), "size": f.get("size")} for f in status.get("files") or [] if f.get("path")]

    async def add_torrent(
        self,
        *,
        torrent: bytes | None = None,
        magnet: str | None = None,
        save_path: str | None = None,
        category: str | None = None,
        paused: bool = False,
    ) -> None:
        """Deluge n'expose pas d'export .torrent : la restauration passe par un
        magnet dans la quasi-totalité des cas. Le label (équivalent de la
        catégorie qBittorrent) vient d'un plugin optionnel et n'est donc pas
        réappliqué."""
        options: dict[str, Any] = {"add_paused": paused}
        if save_path:
            options["download_location"] = save_path
        if torrent:
            payload = base64.b64encode(torrent).decode("ascii")
            await self._rpc("core.add_torrent_file", ["restore.torrent", payload, options])
            return
        if not magnet:
            raise ValueError("Ni fichier .torrent ni magnet fourni.")
        await self._rpc("core.add_torrent_magnet", [magnet, options])

    async def delete_torrents(self, hashes: list[str], delete_files: bool) -> None:
        if not hashes:
            return
        try:
            failures = await self._rpc("core.remove_torrents", [hashes, delete_files])
        except RuntimeError:
            # Deluge 1 n'a pas `remove_torrents` (pluriel) : repli torrent par torrent.
            for torrent_hash in hashes:
                await self._rpc("core.remove_torrent", [torrent_hash, delete_files])
            return
        if failures:
            details = ", ".join(str(failure) for failure in failures)
            raise RuntimeError(f"Deluge n'a pas supprimé tous les torrents : {details}")
