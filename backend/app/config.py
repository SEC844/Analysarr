import os
from pathlib import Path

# Seules ces deux valeurs restent des variables d'environnement (infra Docker).
# Toute la configuration applicative vit en base SQLite (table settings).
PORT = int(os.getenv("PORT", "1818"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/analysarr.db")

# Cache disque des jaquettes, à côté de la base — dans le même volume
# persistant (survit aux redémarrages), évite de retélécharger depuis Emby à
# chaque affichage. Voir services/poster_cache.py.
POSTER_CACHE_DIR = Path(DATABASE_PATH).resolve().parent / "posters"

# Identité du build, injectée par l'image Docker (build-args de la CI) — jamais
# réglée à la main. "dev" hors image (développement local).
APP_VERSION = os.getenv("APP_VERSION") or "dev"
APP_REVISION = os.getenv("APP_REVISION") or None
APP_BUILD_DATE = os.getenv("APP_BUILD_DATE") or None

# Dépôt officiel, en dur : seule source interrogée pour détecter une nouvelle
# version (aucune URL fournie par l'utilisateur, donc aucun risque de SSRF).
GITHUB_REPOSITORY = "SEC844/Analysarr"
