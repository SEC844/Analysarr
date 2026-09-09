export type MediaStatus = "doublon" | "orphelin_qbit" | "non_hardlink" | "tracker_unique" | "manquant_emby" | "manquant_qbit"
export type MediaTypeFilter = "movie" | "series"

export interface MediaListItem {
  id: number
  media_type: MediaTypeFilter
  title: string
  year: number | null
  statuses: MediaStatus[]
  reclaimable_bytes: number
  has_poster: boolean
  poster_image_tag: string | null
  last_scanned_at: string
}

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

export interface MediaDetail extends MediaListItem {
  radarr_id: number | null
  sonarr_id: number | null
  emby_item_id: string | null
  files: MediaFileRead[]
  torrents: TorrentRead[]
  missing_emby_episodes: string[]
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
  status?: MediaStatus | "sain"
  media_type?: MediaTypeFilter
  search?: string
  sort?: "title" | "year" | "size"
}

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
