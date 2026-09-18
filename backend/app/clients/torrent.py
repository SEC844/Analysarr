"""Client torrent configuré : qBittorrent (défaut), Deluge ou Transmission.

Tout le reste de l'application passe par `torrent_client(settings)` et ne
connaît que le format commun décrit dans `torrent_base` — aucun module métier
ne sait quel client est réellement utilisé. Les identifiants restent stockés
dans les champs historiques `Settings.qbittorrent_*` (renommer les colonnes
casserait les configurations existantes) ; `Settings.torrent_client` dit
lequel des trois les utilise."""

from typing import TYPE_CHECKING

from app.clients.deluge import DelugeClient
from app.clients.qbittorrent import QbittorrentClient
from app.clients.torrent_base import TorrentAuthError, TorrentClient
from app.clients.transmission import TransmissionClient

if TYPE_CHECKING:
    from app.models.settings import Settings

__all__ = [
    "TORRENT_CLIENT_KINDS",
    "TORRENT_CLIENT_NAMES",
    "TorrentAuthError",
    "TorrentClient",
    "build_torrent_client",
    "torrent_client",
    "torrent_client_configured",
    "torrent_client_kind",
    "torrent_client_name",
]

TORRENT_CLIENT_KINDS = ("qbittorrent", "deluge", "transmission")
TORRENT_CLIENT_NAMES = {"qbittorrent": "qBittorrent", "deluge": "Deluge", "transmission": "Transmission"}
DEFAULT_KIND = "qbittorrent"


def torrent_client_kind(settings: "Settings | None") -> str:
    kind = getattr(settings, "torrent_client", None) if settings is not None else None
    return kind if kind in TORRENT_CLIENT_KINDS else DEFAULT_KIND


def torrent_client_name(settings: "Settings | None") -> str:
    return TORRENT_CLIENT_NAMES[torrent_client_kind(settings)]


def credentials_required(kind: str) -> tuple[bool, bool]:
    """(identifiant requis, mot de passe requis) — Deluge n'a qu'un mot de
    passe pour son interface web, Transmission peut n'avoir aucune
    authentification."""
    if kind == "deluge":
        return False, True
    if kind == "transmission":
        return False, False
    return True, True


def torrent_client_configured(settings: "Settings | None") -> bool:
    if settings is None or not settings.qbittorrent_url:
        return False
    needs_username, needs_password = credentials_required(torrent_client_kind(settings))
    if needs_username and not settings.qbittorrent_username:
        return False
    return bool(settings.qbittorrent_password) if needs_password else True


def build_torrent_client(kind: str, url: str, username: str | None, password: str | None) -> TorrentClient:
    if kind == "deluge":
        return DelugeClient(url, password or "")
    if kind == "transmission":
        return TransmissionClient(url, username, password)
    return QbittorrentClient(url, username or "", password or "")


def torrent_client(settings: "Settings") -> TorrentClient:
    """Client prêt à l'emploi (`async with`). L'appelant vérifie d'abord
    `torrent_client_configured`."""
    return build_torrent_client(
        torrent_client_kind(settings),
        settings.qbittorrent_url or "",
        settings.qbittorrent_username,
        settings.qbittorrent_password,
    )
