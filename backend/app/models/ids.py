"""Identifiant d'une ligne déjà enregistrée.

SQLModel déclare `id: int | None` : None tant que la ligne n'a pas été insérée.
Une ligne relue en base ou tout juste enregistrée a toujours un identifiant ;
`row_id` le dit au typage, et échoue clairement sur une ligne jamais
enregistrée (erreur de l'appelant) plutôt que de propager un None."""

from typing import Protocol


class _Row(Protocol):
    @property
    def id(self) -> int | None: ...


def row_id(row: _Row) -> int:
    if row.id is None:
        raise ValueError(f"{type(row).__name__} n'a pas encore été enregistré en base")
    return row.id
