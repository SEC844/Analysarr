"""Périmètres d'analyse, partagés par le scan complet et les analyses
partielles. Module minuscule et sans dépendance : il évite un import
circulaire entre `services/scan.py` et `services/partial_scan.py`."""

# Analyses qui ne relisent qu'une source et ne touchent jamais à la liste des
# médias (voir services/partial_scan.py).
NARROW_SCOPES = ("torrents", "queue", "watch", "seer")

# Périmètres acceptés par l'API :
# - "full"    : tout, comme avant ;
# - "library" : Sonarr/Radarr + gestionnaire de média + torrents + file
#               d'attente ; visionnage et demandes Seer sont conservés ;
# - les quatre périmètres étroits ci-dessus.
SCAN_SCOPES = ("full", "library", *NARROW_SCOPES)

__all__ = ["NARROW_SCOPES", "SCAN_SCOPES"]
