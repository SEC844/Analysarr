from urllib.parse import urlparse

# `None` : Deluge et Transmission ne publient pas d'état par tracker.
STATUS_LABELS = {
    None: "non renseigné",
    0: "désactivé",
    1: "non contacté",
    2: "fonctionnel",
    3: "mise à jour",
    4: "en erreur",
}


def extract_tracker_domain(url: str) -> str | None:
    """None pour les entrées spéciales qBittorrent (** [DHT] **, ** [PeX] **, ** [LSD] **)."""
    if url.startswith("**"):
        return None
    return urlparse(url).hostname


def status_label(status: int | None) -> str:
    return STATUS_LABELS.get(status, "inconnu")
