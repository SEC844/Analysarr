"""Client Transmission : RPC JSON sur `/transmission/rpc`.

Transmission protège son API contre le CSRF avec un jeton de session : la
première requête reçoit `409 Conflict` et l'en-tête
`X-Transmission-Session-Id` à rejouer. Un seul appel `torrent-get` ramène
statuts, fichiers et trackers de tous les torrents, mis en cache pour la durée
de la session comme pour Deluge."""

import base64
from typing import Any

import httpx

from app.clients.torrent_base import TorrentAuthError, TorrentClient, content_path_for

SESSION_HEADER = "X-Transmission-Session-Id"
FIELDS = [
    "hashString",
    "name",
    "downloadDir",
    "totalSize",
    "uploadRatio",
    "peersSendingToUs",
    "peersGettingFromUs",
    "addedDate",
    "doneDate",
    "files",
    "trackers",
    "labels",
]


class TransmissionClient(TorrentClient):
    name = "Transmission"

    def __init__(self, base_url: str, username: str | None, password: str | None):
        self.base_url = base_url.rstrip("/")
        self.username = username or ""
        self.password = password or ""
        self._client: httpx.AsyncClient | None = None
        self._session_id = ""
        self._torrents: dict[str, dict[str, Any]] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("TransmissionClient doit être utilisé via 'async with'.")
        return self._client

    async def __aenter__(self) -> "TransmissionClient":
        auth = (self.username, self.password) if self.username or self.password else None
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0, auth=auth)
        try:
            await self._rpc("session-get", {})
        except BaseException:
            await self.__aexit__()
            raise
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _rpc(self, method: str, arguments: dict[str, Any]) -> dict[str, Any]:
        body = {"method": method, "arguments": arguments}
        resp = await self.client.post("/transmission/rpc", json=body, headers={SESSION_HEADER: self._session_id})
        if resp.status_code == 409:
            # Jeton anti-CSRF : fourni par la réponse, à rejouer tel quel.
            self._session_id = resp.headers.get(SESSION_HEADER, "")
            resp = await self.client.post("/transmission/rpc", json=body, headers={SESSION_HEADER: self._session_id})
        if resp.status_code in (401, 403):
            raise TorrentAuthError(f"Identifiants Transmission refusés ({resp.status_code}).")
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("result") != "success":
            raise RuntimeError(f"Transmission a refusé {method} : {payload.get('result')}")
        return payload.get("arguments") or {}

    def _normalize(self, torrent: dict[str, Any]) -> dict[str, Any]:
        save_path = torrent.get("downloadDir")
        paths = [f.get("name") for f in torrent.get("files") or [] if f.get("name")]
        labels = torrent.get("labels") or []
        return {
            "hash": torrent.get("hashString", ""),
            "name": torrent.get("name", ""),
            "save_path": save_path,
            "content_path": content_path_for(save_path, paths),
            "category": labels[0] if labels else None,
            "size": torrent.get("totalSize"),
            "ratio": torrent.get("uploadRatio"),
            "num_seeds": torrent.get("peersSendingToUs"),
            "num_leechs": torrent.get("peersGettingFromUs"),
            "added_on": torrent.get("addedDate"),
            # `doneDate` vaut 0 tant que le téléchargement n'est pas terminé.
            "completion_on": torrent.get("doneDate") or None,
        }

    async def get_torrents(self) -> list[dict[str, Any]]:
        arguments = await self._rpc("torrent-get", {"fields": FIELDS})
        torrents = arguments.get("torrents") or []
        self._torrents = {t.get("hashString", "").lower(): t for t in torrents}
        return [self._normalize(t) for t in torrents]

    async def _torrent(self, torrent_hash: str) -> dict[str, Any]:
        cached = self._torrents.get(torrent_hash.lower())
        if cached is not None:
            return cached
        arguments = await self._rpc("torrent-get", {"ids": [torrent_hash], "fields": FIELDS})
        torrents = arguments.get("torrents") or []
        return torrents[0] if torrents else {}

    async def get_trackers(self, torrent_hash: str) -> list[dict[str, Any]]:
        torrent = await self._torrent(torrent_hash)
        # Transmission ne publie pas d'état par tracker exploitable ici.
        return [{"url": tracker.get("announce", ""), "status": None} for tracker in torrent.get("trackers") or []]

    async def get_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        torrent = await self._torrent(torrent_hash)
        return [{"name": f.get("name"), "size": f.get("length")} for f in torrent.get("files") or [] if f.get("name")]

    async def add_torrent(
        self,
        *,
        torrent: bytes | None = None,
        magnet: str | None = None,
        save_path: str | None = None,
        category: str | None = None,
        paused: bool = False,
    ) -> None:
        """Transmission n'a ni export .torrent ni catégorie : restauration par
        magnet, dans le dossier d'origine."""
        payload: dict[str, Any] = {"paused": paused}
        if save_path:
            payload["download-dir"] = save_path
        if torrent:
            payload["metainfo"] = base64.b64encode(torrent).decode("ascii")
        elif magnet:
            payload["filename"] = magnet
        else:
            raise ValueError("Ni fichier .torrent ni magnet fourni.")
        await self._rpc("torrent-add", payload)

    async def delete_torrents(self, hashes: list[str], delete_files: bool) -> None:
        if not hashes:
            return
        await self._rpc("torrent-remove", {"ids": hashes, "delete-local-data": delete_files})
