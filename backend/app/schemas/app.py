from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

Language = Literal["fr", "en"]


class UpdateStatus(BaseModel):
    """Résultat de la dernière vérification de mise à jour (GitHub Releases)."""

    checked_at: datetime
    latest_version: Optional[str] = None
    release_url: Optional[str] = None
    published_at: Optional[datetime] = None
    # False si la version courante n'est pas une version publiée (build "dev")
    # ou si la vérification a échoué.
    update_available: bool = False
    error: Optional[str] = None


class AppInfo(BaseModel):
    version: str
    revision: Optional[str]
    build_date: Optional[str]
    repository_url: str
    language: Optional[Language]
    update_check_enabled: bool
    # None tant qu'aucune vérification n'a eu lieu (désactivée et jamais
    # lancée manuellement).
    update: Optional[UpdateStatus]


class AppPreferencesWrite(BaseModel):
    language: Language
    update_check_enabled: bool
