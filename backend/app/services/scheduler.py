from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session

_JOB_ID = "periodic_scan"
_UPDATE_JOB_ID = "update_watch"
_UPDATE_INTERVAL_HOURS = 3

scheduler = AsyncIOScheduler()


async def _run_scheduled_scan() -> None:
    from app.services.scan import is_scan_running, run_scan  # import différé : évite un cycle au chargement du module

    if is_scan_running():
        return  # un scan (manuel ou planifié) est déjà en cours, on ne chevauche jamais
    await run_scan(trigger="scheduled")


async def _run_update_watch() -> None:
    from app.services.updates import notify_update_available

    await notify_update_available()


def configure_update_watch(enabled: bool) -> None:
    """Vérification périodique des mises à jour, au seul profit des
    notifications : l'interface, elle, vérifie à l'ouverture d'une page."""
    if scheduler.get_job(_UPDATE_JOB_ID) is not None:
        scheduler.remove_job(_UPDATE_JOB_ID)
    if enabled:
        scheduler.add_job(_run_update_watch, "interval", hours=_UPDATE_INTERVAL_HOURS, id=_UPDATE_JOB_ID)


def refresh_update_watch(session: Session) -> None:
    """(Re)planifie la vérification périodique : uniquement si la vérification
    des mises à jour est active ET qu'au moins un canal est abonné à
    l'événement. Personne d'abonné = aucune requête sortante périodique."""
    from app.models.settings import Settings
    from app.services.notifications import event_has_subscriber

    settings = session.get(Settings, 1)
    checking = settings.update_check_enabled if settings else True  # même défaut que GET /api/app/info
    enabled = checking and event_has_subscriber(session, "update_available")
    configure_update_watch(enabled)


def configure_scan_schedule(interval_minutes: int | None) -> None:
    """(Re)programme le scan périodique. `None` ou une valeur <= 0 retire le
    job. Appelé au démarrage (lifespan) et à chaque sauvegarde des réglages
    pour prendre effet immédiatement sans redémarrer le conteneur."""
    if scheduler.get_job(_JOB_ID) is not None:
        scheduler.remove_job(_JOB_ID)
    if interval_minutes and interval_minutes > 0:
        scheduler.add_job(_run_scheduled_scan, "interval", minutes=interval_minutes, id=_JOB_ID)
