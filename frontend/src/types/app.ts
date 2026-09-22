import type { Language } from "@/i18n"
import type { MediaSort } from "@/types/media"

export interface UpdateStatus {
  checked_at: string
  latest_version: string | null
  release_url: string | null
  published_at: string | null
  update_available: boolean
  error: string | null
}

export type GridSizePreference = "small" | "medium" | "large"

// Miroir de schemas/app.py::UiPreferences (mêmes valeurs par défaut).
export interface UiPreferences {
  card_show_watch: boolean
  card_show_total_size: boolean
  card_show_reclaimable: boolean
  card_show_requested_by: boolean
  library_default_sort: MediaSort
  library_default_grid: GridSizePreference
  media_sections_expanded: boolean
  delete_remove_from_arr_default: boolean
  absolute_dates: boolean
  /** Fuseau d'affichage (nom IANA) ; vide = celui du navigateur. */
  timezone: string
  /** Invitation à mettre une étoile : jamais montrée, reportée, ou terminée. */
  star_prompt_state: "pending" | "later" | "done"
  /** Première ouverture, puis date du dernier report. */
  star_prompt_at: string | null
}

export const DEFAULT_UI_PREFERENCES: UiPreferences = {
  card_show_watch: true,
  card_show_total_size: false,
  card_show_reclaimable: true,
  card_show_requested_by: false,
  library_default_sort: "title",
  library_default_grid: "medium",
  media_sections_expanded: false,
  delete_remove_from_arr_default: false,
  absolute_dates: false,
  timezone: "",
  star_prompt_state: "pending",
  star_prompt_at: null,
}

export interface AppInfo {
  version: string
  revision: string | null
  build_date: string | null
  repository_url: string
  language: Language | null
  update_check_enabled: boolean
  update: UpdateStatus | null
  ui: UiPreferences
}

export interface AppPreferences {
  language: Language
  update_check_enabled: boolean
  ui?: UiPreferences
}
