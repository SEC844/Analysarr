"""Journal d'accès d'uvicorn sans le bruit des vérifications de santé
(issue #51) : le HEALTHCHECK Docker appelle /api/health toutes les 30 s, soit
près de 3 000 lignes par jour qui noient tout le reste.

Seules les vérifications RÉUSSIES disparaissent : un healthcheck en échec
(503, 500…) reste au journal, c'est précisément ce qu'on veut y lire. Le
filtre se pose sur le logger `uvicorn.access` lui-même, donc il vaut pour
le HEALTHCHECK de l'image comme pour une sonde externe (Uptime Kuma…), et il
n'enlève rien d'autre : chaque autre requête est journalisée comme avant."""

import logging

__all__ = ["HEALTH_PATHS", "QuietHealthChecks", "install_access_log_filter"]

ACCESS_LOGGER = "uvicorn.access"
HEALTH_PATHS = frozenset({"/api/health"})


class QuietHealthChecks(logging.Filter):
    """Écarte les lignes d'accès des vérifications de santé réussies.

    Lit les arguments de la ligne d'uvicorn (`'%s - "%s %s HTTP/%s" %d'` :
    client, méthode, chemin avec paramètres, version HTTP, code) plutôt que le
    texte formaté. Une ligne d'une autre forme passe toujours : en cas de
    changement de format côté uvicorn, on perd le filtre, jamais un journal."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True
        path, status = args[2], args[4]
        if not isinstance(path, str) or not isinstance(status, int):
            return True
        return not (path.partition("?")[0] in HEALTH_PATHS and status < 400)


def install_access_log_filter() -> None:
    """Pose le filtre une seule fois (le démarrage peut être rejoué, en test
    par exemple). À appeler après la configuration des journaux d'uvicorn,
    donc au démarrage de l'application."""
    logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(existing, QuietHealthChecks) for existing in logger.filters):
        logger.addFilter(QuietHealthChecks())
