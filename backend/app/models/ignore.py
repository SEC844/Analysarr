from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class IgnoreRule(SQLModel, table=True):
    """Élément que l'utilisateur a choisi d'ignorer : un torrent, un fichier
    de bibliothèque gardé volontairement, ou une alerte d'un média. Table de
    CONFIGURATION : jamais vidée avec le cache média (voir services/ignores.py).

    La règle ne vaut que tant que la situation ignorée reste la même
    (`fingerprint`) : dès qu'elle change, la règle est retirée et l'alerte
    revient."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow)

    # torrent | file | status
    kind: str = Field(index=True)
    # Identité stable du média (services/ignores.py::media_key) : les ids de la
    # table `media` sont régénérés à chaque scan complet.
    media_key: str = Field(index=True)
    # Copiés à la création : affichés même si le média a disparu depuis.
    media_title: str
    media_type: str
    # Hash du torrent (minuscules), chemin du fichier, ou statut masqué.
    target: str = Field(index=True)
    # Libellé lisible de la cible (nom du torrent, chemin, statut).
    label: str = ""
    # Situation au moment où elle a été ignorée. None : pas encore évaluée,
    # elle le sera à la prochaine analyse qui voit la cible.
    fingerprint: str | None = None
    note: str | None = None
