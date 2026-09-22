/** Couverture tracker : information affichée à côté de « Sain ». */
export const INFO_STATUSES: MediaStatus[] = ["tracker_unique", "cross_seed"]

/** Statuts d'alerte : ceux qui demandent une action. La couverture tracker
 * (`tracker_unique`, `cross_seed`) est informative et n'en fait pas partie —
 * même découpage que `services/scan.py::INFO_STATUSES`. */
export const ALERT_STATUSES = [
  "doublon",
  "orphelin_qbit",
  "non_hardlink",
  "manquant_emby",
  "manquant_qbit",
  "manquant_arr",
  "import_rate",
  "telechargement_bloque",
] as const

export type MediaStatus =
  | "doublon"
  | "orphelin_qbit"
  | "non_hardlink"
  | "tracker_unique"
  | "manquant_emby"
  | "manquant_qbit"
  | "manquant_arr"
  | "cross_seed"
  | "import_rate"
  | "telechargement_bloque"
export type MediaTypeFilter = "movie" | "series"

export interface MediaListItem {
  id: number
  media_type: MediaTypeFilter
  title: string
  year: number | null
  statuses: MediaStatus[]
  reclaimable_bytes: number
  total_size: number
  has_poster: boolean
  poster_image_tag: string | null
  last_scanned_at: string
  has_emby_item: boolean
  date_added: string | null
  watch_user_count: number
  watch_played_count: number
  watch_in_progress_count: number
  last_played_at: string | null
  requested_by: string | null
  // Instance Sonarr/Radarr supplémentaire (ex : « Radarr 4K ») ; null = principale.
  arr_instance_name: string | null
}

export interface SeerUserRead {
  name: string
  emby_user_id: string | null
  image_tag: string | null
}

export type MediaRequestStatus = "pending" | "approved" | "declined" | "failed" | "completed"

export interface MediaRequestRead {
  status: MediaRequestStatus
  is_4k: boolean
  seasons: number[]
  requested_at: string | null
  requested_by: SeerUserRead | null
  modified_by: SeerUserRead | null
  auto_approved: boolean
}

export interface WatchUser {
  id: string
  name: string
  image_tag: string | null
  played: boolean
  in_progress: boolean
  // Films : pourcentage de lecture (0-100). Séries : nombre d'épisodes vus.
  progress: number
  last_played_at: string | null
}

export interface MediaWatchStats {
  available: boolean
  live: boolean
  total_episodes: number | null
  users: WatchUser[]
  played_count: number
  in_progress_count: number
  last_played_at: string | null
  last_played_by: string | null
  date_added: string | null
}

export interface EmbyUserRead {
  id: string
  name: string
  image_tag: string | null
  is_disabled: boolean
}

export type WatchFilter = "never" | "in_progress" | "all"

export interface MediaListResponse {
  items: MediaListItem[]
  total: number
}

export interface MediaFileRead {
  id: number
  path: string
  size: number | null
  episode_label: string | null
  is_current: boolean
}

export interface TrackerRead {
  domain: string
  status: string
}

export interface TorrentRead {
  id: number
  hash: string
  name: string
  save_path: string | null
  content_path: string | null
  size: number | null
  is_cross_seed: boolean
  is_hardlinked: boolean | null
  matched_by_name: boolean
  repairable: boolean
  ratio: number | null
  seeders: number | null
  leechers: number | null
  added_on: string | null
  completed_on: string | null
  trackers: TrackerRead[]
}

export interface ImportIssueRead {
  id: number
  // "import" : rangement impossible, relançable. "stalled" : téléchargement en
  // souffrance, purement informatif.
  kind: "import" | "stalled"
  title: string
  // trackedDownloadState de Sonarr/Radarr : importBlocked, importFailed…
  state: string
  reason: string
  size: number | null
  episode_label: string
  can_retry: boolean
}

// Périmètres d'analyse acceptés par l'API (liste fermée côté backend :
// services/scan_scopes.py).
export const SCAN_SCOPES = [
  "full",
  "radarr",
  "sonarr",
  "media_server",
  "torrents",
  "queue",
  "watch",
  "seer",
] as const
export type ScanScope = (typeof SCAN_SCOPES)[number]

export interface MediaRescanResult {
  // Vrai si Sonarr/Radarr ne suit plus ce média : la fiche a été supprimée.
  media_deleted: boolean
  files: number
  torrents: number
  import_issues: number
  statuses: MediaStatus[]
}

export interface ImportRetryResult {
  steps: DeleteStepResult[]
  imported_files: number
}

export interface MediaDetail extends MediaListItem {
  radarr_id: number | null
  sonarr_id: number | null
  emby_item_id: string | null
  /** Identifiants externes, utiles surtout pour un média non suivi. */
  tmdb_id: number | null
  tvdb_id: number | null
  imdb_id: string | null
  files: MediaFileRead[]
  torrents: TorrentRead[]
  missing_emby_episodes: string[]
  requests: MediaRequestRead[]
  import_issues: ImportIssueRead[]
}

/** Fiche Sonarr/Radarr proposée pour rattacher un média non suivi. */
export interface ArrCandidate {
  key: string
  title: string
  year: number | null
  tmdb_id: number | null
  tvdb_id: number | null
  imdb_id: string | null
  /** "certain" : identifiant résolu par Sonarr/Radarr, titre et année concordants. */
  confidence: "certain" | "probable"
}

export type ArrMonitor = "all" | "existing" | "future" | "none"

export interface ArrLinkPreview {
  service: "radarr" | "sonarr"
  instance_name: string
  candidates: ArrCandidate[]
  /** Dossiers que Sonarr/Radarr voit sans média rattaché (ses chemins à lui). */
  folders: string[]
  suggested_folder: string | null
  quality_profiles: { id: number; name: string }[]
  suggested_profile: number | null
}

export type ArrAvailability = "announced" | "inCinemas" | "released"

export interface ArrLinkRequest {
  candidate_key: string
  quality_profile_id: number
  folder: string
  monitor: ArrMonitor
  minimum_availability?: ArrAvailability
}

export interface ArrLinkResult {
  title: string
  service: "radarr" | "sonarr"
}

export interface DeletePreviewItem {
  kind: "duplicate_file" | "orphan_torrent"
  label: string
  size: number | null
}

export interface DeletePreview {
  items: DeletePreviewItem[]
  total_reclaimable_bytes: number
}

export interface DeleteStepResult {
  kind: string
  label: string
  success: boolean
  error: string | null
}

export interface DeleteExecuteResult {
  steps: DeleteStepResult[]
}

export interface MediaDeleteSelection {
  torrent_ids: number[]
  media_file_ids: number[]
  remove_from_arr: boolean
}

export interface DiskUnit {
  size: number
  links: number
}

export interface DeleteFootprintItem {
  id: number
  units: number[]
}

export interface MediaDeleteFootprint {
  units: DiskUnit[]
  torrents: DeleteFootprintItem[]
  files: DeleteFootprintItem[]
}

export interface MediaDeleteSelectionResult {
  steps: DeleteStepResult[]
  media_deleted: boolean
}

export interface CrossSeedSearchResult {
  triggered: number
  errors: string[]
}

export interface HardlinkRepairItem {
  media_file_id: number
  episode_label: string | null
  torrent_id: number
  torrent_name: string
  direction: "torrent_to_library" | "library_to_torrent"
  source_path: string
  target_path: string
  target_exists: boolean
  size: number | null
}

export interface HardlinkRepairPreview {
  items: HardlinkRepairItem[]
  unmatched_torrents: string[]
}

export interface HardlinkRepairStepResult {
  media_file_id: number
  label: string
  success: boolean
  error: string | null
  used_symlink: boolean
}

export interface HardlinkRepairResult {
  steps: HardlinkRepairStepResult[]
}

export interface MediaListParams {
  /** Statuts cochés dans le panneau de filtres (plusieurs possibles). */
  status?: MediaStatus[]
  /** "all" : le média porte TOUS les statuts cochés ; sinon au moins un. */
  match?: "all"
  health?: "sain" | "alerte"
  media_type?: MediaTypeFilter
  watch?: WatchFilter[]
  search?: string
  sort?: MediaSort
}

export type MediaSort = "title" | "year" | "size" | "last_played" | "cleanup"

export type ScanRunStatus = "running" | "completed" | "failed"

export interface ScanRunRead {
  id: number
  started_at: string
  finished_at: string | null
  status: ScanRunStatus
  error_message: string | null
  media_count: number
  duplicate_count: number
  orphan_count: number
  tracker_unique_count: number
  qbittorrent_torrent_count: number
  qbittorrent_matched_count: number
  trigger: "manual" | "scheduled"
  // Périmètre analysé : "full" pour une analyse complète.
  scope: ScanScope
}

export interface ScanEvent {
  type: "started" | "progress" | "completed" | "failed"
  run_id?: number
  stage?: string
  message?: string
  media_count?: number
  duplicate_count?: number
  orphan_count?: number
  qbittorrent_torrent_count?: number
  qbittorrent_matched_count?: number
}
