/** Temps réel (backend : services/realtime/, routers/realtime.py). */
export type ArrService = "sonarr" | "radarr"

export interface WebhookRead {
  service: ArrService
  /** 0 = instance principale. */
  instance_id: number
  name: string
  connected: boolean
  url: string | null
}

export interface RealtimeSettings {
  debounce_seconds: number
  torrents_enabled: boolean
  torrents_interval: number
  torrents_available: boolean
  media_server_enabled: boolean
  media_server_interval: number
  media_server_available: boolean
  analysarr_url: string
  webhooks: WebhookRead[]
  debounce_bounds: [number, number]
  torrent_interval_bounds: [number, number]
  media_server_interval_bounds: [number, number]
}

export interface RealtimeSettingsWrite {
  debounce_seconds: number
  torrents_enabled: boolean
  torrents_interval: number
  media_server_enabled: boolean
  media_server_interval: number
}

export interface WebhookPreview {
  name: string
  url: string
  events: string[]
}

export type SourceState = "active" | "waiting" | "error"

export interface SourceStatus {
  /** `webhook:sonarr:0`, `torrents`, `media_server`. */
  key: string
  kind: "webhook" | "torrents" | "media_server"
  state: SourceState
  last_event_at: string | null
  last_check_at: string | null
  error: string | null
}

export interface RealtimeStatus {
  active: boolean
  sources: SourceStatus[]
  reconciliation: {
    enabled: boolean
    mode: "interval" | "nightly"
    interval_minutes: number | null
    nightly_hour: number
  }
}

/** Événements du flux `/api/events/stream`. */
export type LiveEvent =
  | { type: "media.updated"; media_id: number; watch?: boolean }
  | { type: "media.removed"; media_id: number }
  | { type: "library.changed"; scope?: string }
  | { type: "cleanup.changed" }
  | { type: "realtime.status" }
  | { type: "resync" }
