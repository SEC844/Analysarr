from datetime import datetime

from pydantic import BaseModel


class ActionStepRead(BaseModel):
    label: str
    success: bool
    error: str | None = None


class ActionLogRead(BaseModel):
    id: int
    created_at: datetime
    action: str
    media_title: str
    media_type: str | None
    # None si la fiche média n'existe plus (supprimée ou rescannée).
    media_id: int | None
    success_count: int
    failure_count: int
    freed_bytes: int | None
    details: list[ActionStepRead]
