import os

# Seules ces deux valeurs restent des variables d'environnement (infra Docker).
# Toute la configuration applicative vit en base SQLite (table settings).
PORT = int(os.getenv("PORT", "8000"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/analysarr.db")
