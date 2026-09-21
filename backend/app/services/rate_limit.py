"""Limitation de débit en mémoire, pour les routes d'authentification.

Le verrouillage de compte (`User.locked_until`) protège le mot de passe ; il
ne protège ni le coût d'un bcrypt par requête, ni l'énumération de noms
d'utilisateur, ni les routes 2FA. Une fenêtre glissante par adresse IP couvre
ces cas sans base de données ni dépendance.

En mémoire, donc remis à zéro au redémarrage du conteneur et non partagé entre
plusieurs instances : suffisant pour une application self-hosted à un seul
processus, et jamais la seule défense (le verrouillage de compte reste)."""

import time
from collections import deque

# Fenêtre et plafond : une connexion demande 1 à 2 requêtes, un humain qui se
# trompe plusieurs fois reste très loin du plafond.
WINDOW_SECONDS = 60.0
MAX_REQUESTS = 15
# Garde-fou mémoire : au-delà, les clés les plus anciennes sont oubliées.
MAX_KEYS = 2048

_hits: dict[str, deque[float]] = {}


def reset() -> None:
    _hits.clear()


def retry_after(key: str, limit: int = MAX_REQUESTS, window: float = WINDOW_SECONDS) -> int | None:
    """Enregistre une tentative et renvoie le nombre de secondes à attendre si
    le plafond est atteint, sinon None."""
    now = time.monotonic()
    hits = _hits.setdefault(key, deque())
    while hits and now - hits[0] > window:
        hits.popleft()
    if len(hits) >= limit:
        return max(1, int(window - (now - hits[0])) + 1)
    hits.append(now)
    if len(_hits) > MAX_KEYS:
        _forget_oldest()
    return None


def _forget_oldest() -> None:
    oldest = sorted(_hits.items(), key=lambda item: item[1][-1] if item[1] else 0)
    for key, _ in oldest[: len(_hits) - MAX_KEYS + 1]:
        _hits.pop(key, None)
