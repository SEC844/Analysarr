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

export interface SettingsRead {
  configured: boolean
  emby: ServiceApiKeyRead
  sonarr: ServiceApiKeyRead
  radarr: ServiceApiKeyRead
  qbittorrent: QbittorrentRead
  paths: PathsRead
  cross_seed: CrossSeedRead
}

export interface SettingsWrite {
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
}

export type ServiceName = "emby" | "sonarr" | "radarr" | "qbittorrent" | "cross_seed"

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
  username?: string
  password?: string
}

export interface ConnectionTestResult {
  success: boolean
  message: string
}

export function emptySettingsWrite(): SettingsWrite {
  return {
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
  }
}

export function settingsReadToForm(s: SettingsRead): SettingsWrite {
  return {
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
