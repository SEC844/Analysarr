from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Automation(SQLModel, table=True):
    """Règle d'automatisation, entièrement optionnelle : sans règle créée par
    l'utilisateur, Analysarr n'agit jamais tout seul.

    Configuration utilisateur : cette table n'est jamais supprimée avec le
    cache média (voir database.py)."""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    enabled: bool = True
    # orphan_detected | duplicate_detected | non_hardlink_detected
    trigger: str
    # cleanup | repair_hardlinks | cross_seed_search | notify_only
    action: str
    # Conditions JSON (voir schemas/automations.py::AutomationConditions) :
    # type de média, ancienneté de seed, ratio, espace récupérable minimum.
    conditions: str = "{}"
    # Garde-fou : nombre maximum de médias traités à chaque exécution.
    max_actions: int = 5
    # Simulation : la règle liste ce qu'elle ferait sans rien exécuter.
    dry_run: bool = False

    last_run_at: datetime | None = None
    # Nombre de médias traités lors de la dernière exécution.
    last_run_count: int = 0

    created_at: datetime = Field(default_factory=_utcnow)
