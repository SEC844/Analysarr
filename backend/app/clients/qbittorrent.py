import logging
from typing import Any

import httpx

from app.clients.torrent_base import Fingerprint, TorrentAuthError, TorrentClient, fingerprint

logger = logging.getLogger(__name__)


class QbittorrentClient(TorrentClient):
    """Client qBittorrent Web API v2, authentifié par cookie de session.

    Le succès de la connexion se juge sur la présence d'un cookie *SID* plutôt
    que sur le corps de la réponse : les versions récentes de qBittorrent
    renvoient 204 No Content au lieu de l'historique 200 "Ok." (voir
    app/services/connection_test.py pour le même constat sur le testeur de
    connexion des Réglages).

    C'est le client de référence : sa forme de données est celle que Deluge et
    Transmission reproduisent (voir clients/torrent_base.py).
    """

    name = "qBittorrent"

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._client: httpx.AsyncClient | None = None
        # qBittorrent 4.5.1 à 4.6 : `is_private` n'existe que dans les
        # propriétés d'un torrent. Passé à False dès qu'une réponse ne le porte
        # pas (version plus ancienne) : inutile de le redemander pour chaque
        # torrent.
        self._properties_have_private = True
        # Temps réel : état reconstruit par `sync/maindata` (voir fingerprints).
        self._sync_rid = 0
        self._sync_state: dict[str, dict[str, Any]] = {}

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
            raise TorrentAuthError("Authentification qBittorrent refusée.")
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

    async def fingerprints(self) -> dict[str, Fingerprint]:
        """`sync/maindata` : seuls les champs modifiés depuis l'appel précédent
        (`rid`) reviennent — l'interface web de qBittorrent l'interroge ainsi
        chaque seconde. Réponse complète (`full_update`) au premier appel ou
        quand qBittorrent ne peut plus fournir le différentiel."""
        resp = await self.client.get("/api/v2/sync/maindata", params={"rid": self._sync_rid})
        resp.raise_for_status()
        data = resp.json()
        torrents = data.get("torrents") or {}
        if data.get("full_update"):
            self._sync_state = {h.lower(): dict(v) for h, v in torrents.items()}
        else:
            for torrent_hash, changes in torrents.items():
                self._sync_state.setdefault(torrent_hash.lower(), {}).update(changes)
            for torrent_hash in data.get("torrents_removed") or []:
                self._sync_state.pop(str(torrent_hash).lower(), None)
        self._sync_rid = int(data.get("rid") or 0)
        return {
            torrent_hash: fingerprint(
                t.get("name"),
                t.get("save_path"),
                t.get("content_path"),
                t.get("category"),
                (t.get("progress") or 0) >= 1,
            )
            for torrent_hash, t in self._sync_state.items()
        }

    async def private_flag(self, torrent: dict[str, Any]) -> bool | None:
        """Vérifié dans le code de qBittorrent : depuis la 5.0 (Web API 2.11),
        `private` figure dans `torrents/info` (null sans métadonnées) ; de la
        4.5.1 à la 4.6, seul `torrents/properties` donne `is_private` ; avant,
        l'information n'est pas publiée."""
        if "private" in torrent:
            return await super().private_flag(torrent)
        if not self._properties_have_private:
            return None
        resp = await self.client.get("/api/v2/torrents/properties", params={"hash": torrent["hash"]})
        resp.raise_for_status()
        value = resp.json().get("is_private")
        if not isinstance(value, bool):
            self._properties_have_private = False
            return None
        return value

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

    async def export_torrent(self, torrent_hash: str) -> bytes | None:
        """`torrents/export` existe depuis qBittorrent 4.2 ; un client plus
        ancien répond en erreur, auquel cas on repassera par un magnet."""
        try:
            resp = await self.client.get("/api/v2/torrents/export", params={"hash": torrent_hash})
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.debug("Export du torrent %s impossible, repli sur le magnet", torrent_hash, exc_info=True)
            return None
        return resp.content or None

    async def add_torrent(
        self,
        *,
        torrent: bytes | None = None,
        magnet: str | None = None,
        save_path: str | None = None,
        category: str | None = None,
        paused: bool = False,
    ) -> None:
        data: dict[str, str] = {
            # `paused` jusqu'à qBittorrent 4, `stopped` depuis la 5 : les deux
            # sont envoyés, un paramètre inconnu est ignoré.
            "paused": str(paused).lower(),
            "stopped": str(paused).lower(),
            # Les fichiers sont déjà en place : on laisse la vérification se
            # faire, sinon le torrent repart en téléchargement.
            "skip_checking": "false",
            "autoTMM": "false",
        }
        if save_path:
            data["savepath"] = save_path
        if category:
            data["category"] = category
        files = None
        if torrent:
            files = {"torrents": ("restore.torrent", torrent, "application/x-bittorrent")}
        elif magnet:
            data["urls"] = magnet
        else:
            raise ValueError("Ni fichier .torrent ni magnet fourni.")
        resp = await self.client.post("/api/v2/torrents/add", data=data, files=files)
        resp.raise_for_status()
        if resp.text.strip().lower() == "fails.":
            raise RuntimeError("qBittorrent a refusé le torrent.")

    async def delete_torrents(self, hashes: list[str], delete_files: bool) -> None:
        if not hashes:
            return
        resp = await self.client.post(
            "/api/v2/torrents/delete",
            data={"hashes": "|".join(hashes), "deleteFiles": str(delete_files).lower()},
        )
        resp.raise_for_status()
