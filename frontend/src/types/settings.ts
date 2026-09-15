import type { MediaServer } from "@/i18n"

export interface ServiceApiKeyRead {
  url: string | null
  api_key_set: boolean
}

export interface QbittorrentRead {
  url: string | null
  username: string | null
  password_set: boolean
}

export interface PathsRead {
  emby_library_path: string | null
  qbittorrent_download_path: string | null
}

export interface CrossSeedRead {
  enabled: boolean
  url: string | null
  api_key_set: boolean
  library_path: string | null
}

export interface ScheduleRead {
  enabled: boolean
  interval_minutes: number | null
}

export type NotificationChannel = "discord" | "ntfy" | "gotify"

// Secrets (webhook Discord, sujet ntfy, jetons) : seul leur état est renvoyé.
export interface NotificationsRead {
  discord_set: boolean
  ntfy_set: boolean
  ntfy_token_set: boolean
  gotify_url: string | null
  gotify_token_set: boolean
  on_scan: boolean
  on_scan_failure: boolean
  on_actions: boolean
}

export interface NotificationTestResult {
  // Canal -> message d'erreur, ou null si l'envoi a réussi.
  results: Partial<Record<NotificationChannel, string | null>>
}

export type ArrKind = "sonarr" | "radarr"

export interface ArrInstanceRead {
  id: number
  kind: ArrKind
  name: string
  url: string
  api_key_set: boolean
}

// Instance supplémentaire dans le formulaire. `key` : identifiant local pour
// React (une nouvelle instance n'a pas encore d'id).
export interface ArrInstanceForm {
  key: string
  id: number | null
  kind: ArrKind
  name: string
  url: string
  // Vide : conserve la clé enregistrée.
  api_key: string
}

export interface SettingsRead {
  configured: boolean
  notifications: NotificationsRead
  arr_instances: ArrInstanceRead[]
  media_server: MediaServer
  watch: { excluded_emby_user_ids: string[] }
  emby: ServiceApiKeyRead
  sonarr: ServiceApiKeyRead
  radarr: ServiceApiKeyRead
  qbittorrent: QbittorrentRead
  paths: PathsRead
  cross_seed: CrossSeedRead
  seer: { enabled: boolean; url: string | null; api_key_set: boolean }
  schedule: ScheduleRead
}

export interface SettingsWrite {
  media_server: MediaServer
  emby_url: string
  emby_api_key: string
  sonarr_url: string
  sonarr_api_key: string
  radarr_url: string
  radarr_api_key: string
  qbittorrent_url: string
  qbittorrent_username: string
  qbittorrent_password: string
  emby_library_path: string
  qbittorrent_download_path: string
  cross_seed_enabled: boolean
  cross_seed_url: string
  cross_seed_api_key: string
  cross_seed_library_path: string
  seer_enabled: boolean
  seer_url: string
  seer_api_key: string
  scan_schedule_enabled: boolean
  scan_schedule_interval_minutes: number | null
  notify_discord_webhook: string
  notify_ntfy_url: string
  notify_ntfy_token: string
  notify_gotify_url: string
  notify_gotify_token: string
  notify_clear: NotificationChannel[]
  notify_on_scan: boolean
  notify_on_scan_failure: boolean
  notify_on_actions: boolean
  arr_instances: ArrInstanceForm[]
  excluded_emby_user_ids: string[]
}

export type ServiceName = "emby" | "sonarr" | "radarr" | "qbittorrent" | "cross_seed" | "seer"

export interface BrowseEntry {
  name: string
  path: string
}

export interface BrowseResult {
  path: string
  parent: string | null
  directories: BrowseEntry[]
}

export interface ConnectionTestRequest {
  url?: string
  api_key?: string
  media_server?: MediaServer
  username?: string
  password?: string
}

export interface ConnectionTestResult {
  success: boolean
  message: string
}

export function emptySettingsWrite(): SettingsWrite {
  return {
    media_server: "emby",
    emby_url: "",
    emby_api_key: "",
    sonarr_url: "",
    sonarr_api_key: "",
    radarr_url: "",
    radarr_api_key: "",
    qbittorrent_url: "",
    qbittorrent_username: "",
    qbittorrent_password: "",
    emby_library_path: "",
    qbittorrent_download_path: "",
    cross_seed_enabled: false,
    cross_seed_url: "",
    cross_seed_api_key: "",
    cross_seed_library_path: "",
    seer_enabled: false,
    seer_url: "",
    seer_api_key: "",
    scan_schedule_enabled: false,
    scan_schedule_interval_minutes: null,
    notify_discord_webhook: "",
    notify_ntfy_url: "",
    notify_ntfy_token: "",
    notify_gotify_url: "",
    notify_gotify_token: "",
    notify_clear: [],
    notify_on_scan: false,
    notify_on_scan_failure: true,
    notify_on_actions: true,
    arr_instances: [],
    excluded_emby_user_ids: [],
  }
}

export function settingsReadToForm(s: SettingsRead): SettingsWrite {
  return {
    media_server: s.media_server,
    emby_url: s.emby.url ?? "",
    emby_api_key: "",
    sonarr_url: s.sonarr.url ?? "",
    sonarr_api_key: "",
    radarr_url: s.radarr.url ?? "",
    radarr_api_key: "",
    qbittorrent_url: s.qbittorrent.url ?? "",
    qbittorrent_username: s.qbittorrent.username ?? "",
    qbittorrent_password: "",
    emby_library_path: s.paths.emby_library_path ?? "",
    qbittorrent_download_path: s.paths.qbittorrent_download_path ?? "",
    cross_seed_enabled: s.cross_seed.enabled,
    cross_seed_url: s.cross_seed.url ?? "",
    cross_seed_api_key: "",
    cross_seed_library_path: s.cross_seed.library_path ?? "",
    seer_enabled: s.seer.enabled,
    seer_url: s.seer.url ?? "",
    seer_api_key: "",
    scan_schedule_enabled: s.schedule.enabled,
    scan_schedule_interval_minutes: s.schedule.interval_minutes,
    notify_discord_webhook: "",
    notify_ntfy_url: "",
    notify_ntfy_token: "",
    notify_gotify_url: s.notifications.gotify_url ?? "",
    notify_gotify_token: "",
    notify_clear: [],
    notify_on_scan: s.notifications.on_scan,
    notify_on_scan_failure: s.notifications.on_scan_failure,
    notify_on_actions: s.notifications.on_actions,
    arr_instances: s.arr_instances.map((i) => ({
      key: `saved-${i.id}`,
      id: i.id,
      kind: i.kind,
      name: i.name,
      url: i.url,
      api_key: "",
    })),
    excluded_emby_user_ids: s.watch.excluded_emby_user_ids,
  }
}

export function isCoreConfigComplete(f: SettingsWrite, existing: SettingsRead | undefined): boolean {
  const hasEmbyKey = !!f.emby_api_key || !!existing?.emby.api_key_set
  const hasSonarrKey = !!f.sonarr_api_key || !!existing?.sonarr.api_key_set
  const hasRadarrKey = !!f.radarr_api_key || !!existing?.radarr.api_key_set
  const hasQbitPassword = !!f.qbittorrent_password || !!existing?.qbittorrent.password_set

  return (
    !!f.emby_url &&
    hasEmbyKey &&
    !!f.sonarr_url &&
    hasSonarrKey &&
    !!f.radarr_url &&
    hasRadarrKey &&
    !!f.qbittorrent_url &&
    !!f.qbittorrent_username &&
    hasQbitPassword &&
    !!f.emby_library_path &&
    !!f.qbittorrent_download_path
  )
}
