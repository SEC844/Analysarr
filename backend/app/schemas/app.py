from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Language = Literal["fr", "en"]


class UpdateStatus(BaseModel):
    """Résultat de la dernière vérification de mise à jour (GitHub Releases)."""

    checked_at: datetime
    latest_version: str | None = None
    release_url: str | None = None
    published_at: datetime | None = None
    # False si la version courante n'est pas une version publiée (build "dev")
    # ou si la vérification a échoué.
    update_available: bool = False
    error: str | None = None


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
    # Fuseau horaire d'affichage (nom IANA, ex : "Europe/Paris"). Vide = celui
    # du navigateur. Les dates sont stockées en UTC (l'heure d'un conteneur
    # Docker l'est presque toujours) : sans ce réglage, un accès depuis un
    # autre fuseau afficherait l'heure de ce navigateur, pas celle voulue.
    # Valeur passée telle quelle à `Intl.DateTimeFormat` côté navigateur :
    # format contraint ici, et une valeur inconnue est ignorée à l'affichage.
    timezone: str = Field(default="", max_length=64, pattern=r"^$|^[A-Za-z][A-Za-z0-9_+/-]{0,63}$")
    # Invitation à mettre une étoile sur GitHub : "pending" (jamais montrée),
    # "later" (reportée) ou "done" (l'utilisateur a suivi le lien, plus jamais
    # d'invitation). `star_prompt_at` date la première ouverture puis chaque
    # report, pour espacer l'invitation sans jamais l'afficher à l'arrivée.
    star_prompt_state: Literal["pending", "later", "done"] = "pending"
    star_prompt_at: str | None = None


class AppInfo(BaseModel):
    version: str
    revision: str | None
    build_date: str | None
    repository_url: str
    language: Language | None
    update_check_enabled: bool
    # None tant qu'aucune vérification n'a eu lieu (désactivée et jamais
    # lancée manuellement).
    update: UpdateStatus | None
    ui: UiPreferences


class AppPreferencesWrite(BaseModel):
    language: Language
    update_check_enabled: bool
    # None : préférences d'affichage inchangées.
    ui: UiPreferences | None = None
