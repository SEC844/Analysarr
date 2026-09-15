from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ActionStepRead(BaseModel):
    label: str
    success: bool
    error: Optional[str] = None


class ActionLogRead(BaseModel):
    id: int
    created_at: datetime
    action: str
    media_title: str
    media_type: Optional[str]
    # None si la fiche média n'existe plus (supprimée ou rescannée).
    media_id: Optional[int]
    success_count: int
    failure_count: int
    freed_bytes: Optional[int]
    details: list[ActionStepRead]
