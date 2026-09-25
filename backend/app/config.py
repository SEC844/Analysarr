import logging
import os
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# Emplacements de la base dans l'image Docker : /config depuis la 0.26.0, /data
# avant (au milieu des médias). Hors image (développement local), ./data.
CONTAINER_DATABASE_PATH = "/config/analysarr.db"
LEGACY_DATABASE_PATH = "/data/analysarr.db"
LOCAL_DATABASE_PATH = "./data/analysarr.db"


def resolve_database_path(
    explicit: str | None,
    exists: Callable[[str], bool] = os.path.exists,
) -> str:
    """Chemin de la base SQLite. Une valeur explicite (DATABASE_PATH) gagne
    toujours. Sinon /config, SAUF si une installation existante a sa base dans
    /data et que /config n'en contient pas encore : l'image ne fixe plus
    DATABASE_PATH, et reprendre /config d'office ferait repartir de zéro un
    utilisateur qui met simplement son image à jour (configuration perdue)."""
    if explicit:
        return explicit
    if not exists(CONTAINER_DATABASE_PATH) and exists(LEGACY_DATABASE_PATH):
        logger.warning(
            "Base trouvée dans %s (ancien emplacement, au milieu des médias) : elle reste utilisée. "
            "Pour migrer : arrêtez le conteneur, montez un dossier sur /config, copiez-y analysarr.db, "
            "puis redémarrez. Ou fixez DATABASE_PATH=%s pour garder l'emplacement actuel sans ce message.",
            LEGACY_DATABASE_PATH,
            LEGACY_DATABASE_PATH,
        )
        return LEGACY_DATABASE_PATH
    if exists(os.path.dirname(CONTAINER_DATABASE_PATH)):
        return CONTAINER_DATABASE_PATH
    return LOCAL_DATABASE_PATH


# Seules ces valeurs restent des variables d'environnement (infra Docker).
# Toute la configuration applicative vit en base SQLite (table settings).
PORT = int(os.getenv("PORT", "1818"))
DATABASE_PATH = resolve_database_path(os.getenv("DATABASE_PATH"))

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
