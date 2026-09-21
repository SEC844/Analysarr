"""Périmètres d'analyse, partagés par le scan complet et les analyses
partielles. Module minuscule et sans dépendance : il évite un import
circulaire entre `services/scan.py` et `services/partial_scan.py`."""

# Analyses qui ne relisent qu'UN service. Aucune ne passe par le scan complet.
#
# - "radarr" / "sonarr"  : films / séries, leurs fichiers suivis et leur file
#   d'attente. Seuls ces deux périmètres peuvent ajouter ou retirer un média,
#   puisque la liste des médias vient de Sonarr/Radarr.
# - "media_server"       : les fichiers de bibliothèque (Emby ou Jellyfin).
# - "torrents"           : le client torrent.
# - "queue"              : la file d'attente Sonarr et Radarr.
# - "watch" / "seer"     : visionnage, demandes.
SERVICE_SCOPES = ("radarr", "sonarr", "media_server", "torrents", "queue", "watch", "seer")

# Périmètres qui ne relisent qu'une source et ne reconstruisent jamais de média.
NARROW_SCOPES = ("torrents", "queue", "watch", "seer")

# Périmètres acceptés par l'API.
SCAN_SCOPES = ("full", *SERVICE_SCOPES)

__all__ = ["NARROW_SCOPES", "SCAN_SCOPES", "SERVICE_SCOPES"]
