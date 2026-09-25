import type { MediaServer, RequestManager } from "@/i18n"

export interface ServiceApiKeyRead {
  url: string | null
  api_key_set: boolean
}

export type TorrentClientKind = "qbittorrent" | "deluge" | "transmission"

// Noms de clients : jamais traduits.
export const TORRENT_CLIENT_NAMES: Record<TorrentClientKind, string> = {
  qbittorrent: "qBittorrent",
  deluge: "Deluge",
  transmission: "Transmission",
}

/** Identifiants réellement exigés par chaque client — même règle que
 * `clients/torrent.py::credentials_required` côté backend : Deluge n'a qu'un
 * mot de passe d'interface web, Transmission peut n'avoir aucune
 * authentification. */
export function torrentCredentialsRequired(kind: TorrentClientKind): { username: boolean; password: boolean } {
  if (kind === "deluge") return { username: false, password: true }
  if (kind === "transmission") return { username: false, password: false }
  return { username: true, password: true }
}

export interface QbittorrentRead {
  client: TorrentClientKind
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

export interface WidgetKeyRead {
  enabled: boolean
  // Renseignée uniquement juste après la génération.
  key: string | null
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
  arr_instances: ArrInstanceRead[]
  media_server: MediaServer
  watch: { excluded_emby_user_ids: string[] }
  emby: ServiceApiKeyRead
  sonarr: ServiceApiKeyRead
  radarr: ServiceApiKeyRead
  qbittorrent: QbittorrentRead
  paths: PathsRead
  cross_seed: CrossSeedRead
  seer: { enabled: boolean; kind: RequestManager; url: string | null; api_key_set: boolean }
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
  torrent_client: TorrentClientKind
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
  seer_type: RequestManager
  seer_url: string
  seer_api_key: string
  scan_schedule_enabled: boolean
  scan_schedule_interval_minutes: number | null
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
  torrent_client?: TorrentClientKind
  api_key?: string
  media_server?: MediaServer
  request_manager?: RequestManager
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
    torrent_client: "qbittorrent",
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
    seer_type: "seer",
    seer_url: "",
    seer_api_key: "",
    scan_schedule_enabled: false,
    scan_schedule_interval_minutes: null,
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
    torrent_client: s.qbittorrent.client,
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
    seer_type: s.seer.kind,
    seer_url: s.seer.url ?? "",
    seer_api_key: "",
    scan_schedule_enabled: s.schedule.enabled,
    scan_schedule_interval_minutes: s.schedule.interval_minutes,
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

// Étapes obligatoires de l'assistant, dans l'ordre où il les présente.
export type CoreStep = "mediaServer" | "sonarr" | "radarr" | "torrentClient" | "paths"

/** Étapes obligatoires encore incomplètes — sert au récapitulatif de
 * l'assistant autant qu'au verrou du bouton « Terminer ». */
export function missingCoreConfig(f: SettingsWrite, existing: SettingsRead | undefined): CoreStep[] {
  const credentials = torrentCredentialsRequired(f.torrent_client)
  const missing: CoreStep[] = []

  if (!f.emby_url || !(f.emby_api_key || existing?.emby.api_key_set)) missing.push("mediaServer")
  if (!f.sonarr_url || !(f.sonarr_api_key || existing?.sonarr.api_key_set)) missing.push("sonarr")
  if (!f.radarr_url || !(f.radarr_api_key || existing?.radarr.api_key_set)) missing.push("radarr")
  if (
    !f.qbittorrent_url ||
    (credentials.username && !f.qbittorrent_username) ||
    (credentials.password && !(f.qbittorrent_password || existing?.qbittorrent.password_set))
  ) {
    missing.push("torrentClient")
  }
  if (!f.emby_library_path || !f.qbittorrent_download_path) missing.push("paths")

  return missing
}

export function isCoreConfigComplete(f: SettingsWrite, existing: SettingsRead | undefined): boolean {
  return missingCoreConfig(f, existing).length === 0
}
