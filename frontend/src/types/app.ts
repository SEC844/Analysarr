import type { Language } from "@/i18n"

export interface UpdateStatus {
  checked_at: string
  latest_version: string | null
  release_url: string | null
  published_at: string | null
  update_available: boolean
  error: string | null
}

export interface AppInfo {
  version: string
  revision: string | null
  build_date: string | null
  repository_url: string
  language: Language | null
  update_check_enabled: boolean
  update: UpdateStatus | null
}

export interface AppPreferences {
  language: Language
  update_check_enabled: boolean
}
