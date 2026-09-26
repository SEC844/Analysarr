from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

IgnoreKind = Literal["torrent", "file", "status"]


class IgnoreCreate(BaseModel):
    """Ce que l'utilisateur ignore, désigné par des identifiants du média —
    jamais par un hash ou un chemin fourni par le client."""

    media_id: int
    kind: IgnoreKind
    torrent_id: int | None = None
    file_id: int | None = None
    # Alertes du média à masquer (kind = status).
    statuses: list[str] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=200)

    @field_validator("note")
    @classmethod
    def _strip_note(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


class IgnoreRuleRead(BaseModel):
    id: int
    kind: IgnoreKind
    # Nom du torrent, chemin du fichier ; statut masqué pour une alerte.
    label: str
    status: str | None
    media_title: str
    media_type: str
    # Fiche actuelle du média, s'il existe encore.
    media_id: int | None
    # La cible a été vue à la dernière analyse (torrent, fichier ou média
    # toujours présent).
    present: bool
    note: str | None
    created_at: datetime


class MutedStatusRead(BaseModel):
    status: str
    rule_id: int
    note: str | None
