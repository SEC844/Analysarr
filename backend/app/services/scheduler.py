from datetime import UTC

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session

from app.database import engine
from app.models.settings import Settings
from app.services.library_history import record_missing_snapshot, record_snapshot
from app.services.notifications import event_has_subscriber
from app.services.scan import is_scan_running, run_scan
from app.services.trash import purge_expired
from app.services.updates import notify_update_available
from app.services.weekly_summary import send_weekly_summary

_JOB_ID = "periodic_scan"
_UPDATE_JOB_ID = "update_watch"
_UPDATE_INTERVAL_HOURS = 3
_TRASH_JOB_ID = "trash_purge"
_TRASH_INTERVAL_HOURS = 6
_WEEKLY_JOB_ID = "weekly_summary"
# Résumé hebdomadaire : le lundi matin, heure du conteneur.
WEEKLY_SUMMARY_DAY = "mon"
WEEKLY_SUMMARY_HOUR = 9
_SNAPSHOT_JOB_ID = "library_snapshot"
_SNAPSHOT_STARTUP_JOB_ID = "library_snapshot_startup"
# Planificateur en retard (machine en veille, boucle chargée) : la mesure du
# jour est encore prise dans l'heure qui suit.
_SNAPSHOT_GRACE_SECONDS = 3600
DEFAULT_NIGHTLY_HOUR = 4

scheduler = AsyncIOScheduler()


async def _run_scheduled_scan() -> None:
    if is_scan_running():
        return  # un scan (manuel ou planifié) est déjà en cours, on ne chevauche jamais
    await run_scan(trigger="scheduled")


async def _run_update_watch() -> None:
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
    settings = session.get(Settings, 1)
    checking = settings.update_check_enabled if settings else True  # même défaut que GET /api/app/info
    enabled = checking and event_has_subscriber(session, "update_available")
    configure_update_watch(enabled)


async def _run_weekly_summary() -> None:
    await send_weekly_summary()


def configure_weekly_summary() -> None:
    """Résumé hebdomadaire : le job tourne chaque lundi, mais ne calcule ni
    n'envoie rien tant qu'aucun canal n'est abonné à l'événement (opt-in)."""
    if scheduler.get_job(_WEEKLY_JOB_ID) is None:
        scheduler.add_job(
            _run_weekly_summary,
            CronTrigger(day_of_week=WEEKLY_SUMMARY_DAY, hour=WEEKLY_SUMMARY_HOUR),
            id=_WEEKLY_JOB_ID,
            misfire_grace_time=3600,
        )


async def _run_trash_purge() -> None:
    with Session(engine) as session:
        purge_expired(session, session.get(Settings, 1))


def configure_trash_purge() -> None:
    """Rétention de la corbeille appliquée toutes les 6 h (et à la demande,
    voir routers/trash.py). Sans effet quand la corbeille est vide."""
    if scheduler.get_job(_TRASH_JOB_ID) is None:
        scheduler.add_job(_run_trash_purge, "interval", hours=_TRASH_INTERVAL_HOURS, id=_TRASH_JOB_ID)


def configure_scan_schedule(
    interval_minutes: int | None, mode: str = "interval", nightly_hour: int = DEFAULT_NIGHTLY_HOUR
) -> None:
    """(Re)programme le scan complet. Mode `interval` : toutes les
    `interval_minutes` minutes (`None` ou <= 0 retire le job). Mode
    `nightly` : une fois par nuit à `nightly_hour` heures, heure du conteneur
    — le filet de sécurité du temps réel. Appelé au démarrage (lifespan) et à
    chaque sauvegarde des réglages : effet immédiat, sans redémarrage."""
    if scheduler.get_job(_JOB_ID) is not None:
        scheduler.remove_job(_JOB_ID)
    if mode == "nightly":
        scheduler.add_job(_run_scheduled_scan, CronTrigger(hour=nightly_hour, minute=0), id=_JOB_ID)
    elif interval_minutes and interval_minutes > 0:
        scheduler.add_job(_run_scheduled_scan, "interval", minutes=interval_minutes, id=_JOB_ID)


def configure_scan_schedule_from(settings: Settings | None) -> None:
    """Planification enregistrée (désactivée = aucun scan planifié)."""
    if settings is None or not settings.scan_schedule_enabled:
        configure_scan_schedule(None)
        return
    configure_scan_schedule(
        settings.scan_schedule_interval_minutes, settings.scan_schedule_mode, settings.scan_nightly_hour
    )


def configure_library_snapshots() -> None:
    """Historique de la bibliothèque (services/library_history.py) : une
    photographie par jour, en fin de journée UTC — la dernière mesure du jour
    est celle qui compte —, plus une au démarrage si la journée n'en a pas
    encore (conteneur éteint à l'heure du job). Fonctions synchrones :
    APScheduler les exécute dans un thread, jamais dans la boucle asyncio."""
    if scheduler.get_job(_SNAPSHOT_JOB_ID) is None:
        scheduler.add_job(
            record_snapshot,
            CronTrigger(hour=23, minute=50, timezone=UTC),
            id=_SNAPSHOT_JOB_ID,
            coalesce=True,
            misfire_grace_time=_SNAPSHOT_GRACE_SECONDS,
        )
    if scheduler.get_job(_SNAPSHOT_STARTUP_JOB_ID) is None:
        scheduler.add_job(record_missing_snapshot, id=_SNAPSHOT_STARTUP_JOB_ID)
