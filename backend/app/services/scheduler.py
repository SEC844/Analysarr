from apscheduler.schedulers.asyncio import AsyncIOScheduler

_JOB_ID = "periodic_scan"

scheduler = AsyncIOScheduler()


async def _run_scheduled_scan() -> None:
    from app.services.scan import is_scan_running, run_scan  # import différé : évite un cycle au chargement du module

    if is_scan_running():
        return  # un scan (manuel ou planifié) est déjà en cours, on ne chevauche jamais
    await run_scan(trigger="scheduled")


def configure_scan_schedule(interval_minutes: int | None) -> None:
    """(Re)programme le scan périodique. `None` ou une valeur <= 0 retire le
    job. Appelé au démarrage (lifespan) et à chaque sauvegarde des réglages
    pour prendre effet immédiatement sans redémarrer le conteneur."""
    if scheduler.get_job(_JOB_ID) is not None:
        scheduler.remove_job(_JOB_ID)
    if interval_minutes and interval_minutes > 0:
        scheduler.add_job(_run_scheduled_scan, "interval", minutes=interval_minutes, id=_JOB_ID)
