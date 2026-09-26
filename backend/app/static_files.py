"""Fichiers de l'interface (build du frontend) servis par FastAPI."""

from pathlib import Path


def resolve_static_file(static_dir: Path, requested: str) -> Path | None:
    """Fichier de l'interface demandé, ou None — la page d'accueil prend alors
    le relais (routes de l'application monopage).

    Le chemin est résolu puis doit rester DANS `static_dir`. Faille réelle :
    `/..%2F..%2Fconfig%2Fanalysarr.db` sortait du dossier, sans session, et
    servait n'importe quel fichier lisible du conteneur — base de données et
    clés API comprises. `%2F` est décodé avant le routage, et un navigateur ne
    normalise pas ce que `curl --path-as-is` envoie tel quel."""
    if not requested:
        return None
    root = static_dir.resolve()
    candidate = (root / requested).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate
