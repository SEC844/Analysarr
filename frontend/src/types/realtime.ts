/** Temps réel (backend : services/realtime/, routers/realtime.py) : la
 * norme, sans réglage. */
export type ArrService = "sonarr" | "radarr"

/** `connected` : branché et à jour ; `pending` : essai en cours ou à venir ;
 * `error` : dernier essai refusé ; `no_address` : adresse d'Analysarr inconnue. */
export type WebhookState = "connected" | "pending" | "error" | "no_address"

export interface WebhookRead {
  service: ArrService
  /** 0 = instance principale. */
  instance_id: number
  name: string
  state: WebhookState
  error: string | null
  url: string | null
}

export interface WebhooksRead {
  analysarr_url: string
  webhooks: WebhookRead[]
}

export type SourceState = "active" | "waiting" | "error"

export interface SourceStatus {
  /** `webhook:sonarr:0`, `torrents`, `media_server`, `requests`. */
  key: string
  kind: "webhook" | "torrents" | "media_server" | "requests"
  state: SourceState
  last_event_at: string | null
  last_check_at: string | null
  error: string | null
}

export interface RealtimeStatus {
  active: boolean
  /** Adresse d'Analysarr connue (sinon le navigateur propose la sienne). */
  address_set: boolean
  sources: SourceStatus[]
}

/** Événements du flux `/api/events/stream`. */
export type LiveEvent =
  | { type: "media.updated"; media_id: number; watch?: boolean }
  | { type: "media.removed"; media_id: number }
  | { type: "library.changed"; scope?: string }
  | { type: "cleanup.changed" }
  | { type: "realtime.status" }
  | { type: "resync" }
