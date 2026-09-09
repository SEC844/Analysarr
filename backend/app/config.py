import os
from pathlib import Path

# Seules ces deux valeurs restent des variables d'environnement (infra Docker).
# Toute la configuration applicative vit en base SQLite (table settings).
PORT = int(os.getenv("PORT", "8000"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/analysarr.db")

# Cache disque des jaquettes, à côté de la base — dans le même volume
# persistant (survit aux redémarrages), évite de retélécharger depuis Emby à
# chaque affichage. Voir services/poster_cache.py.
POSTER_CACHE_DIR = Path(DATABASE_PATH).resolve().parent / "posters"
