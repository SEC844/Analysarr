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


class UiPreferences(BaseModel):
    """Préférences d'affichage (Réglages → Préférences). Toute clé absente
    prend sa valeur par défaut : l'ajout d'une préférence ne casse jamais une
    configuration déjà enregistrée."""

    card_show_watch: bool = True
    card_show_total_size: bool = False
    card_show_reclaimable: bool = True
    card_show_requested_by: bool = False
    library_default_sort: Literal["title", "year", "size", "last_played", "cleanup"] = "title"
    library_default_grid: Literal["small", "medium", "large"] = "medium"
    media_sections_expanded: bool = False
    delete_remove_from_arr_default: bool = False
    absolute_dates: bool = False


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
    ui: UiPreferences


class AppPreferencesWrite(BaseModel):
    language: Language
    update_check_enabled: bool
    # None : préférences d'affichage inchangées.
    ui: Optional[UiPreferences] = None
