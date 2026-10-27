/** Temps réel (backend : services/realtime/, routers/realtime.py) : actif
 * par défaut, désactivable dans Réglages → Planification. */
export type ArrService = "sonarr" | "radarr"

/** `connected` : branché et à jour ; `pending` : essai en cours ou à venir ;
 * `error` : dernier essai refusé (branchement ou retrait) ; `no_address` :
 * adresse d'Analysarr inconnue ; `removing` : webhooks désactivés, retrait en
 * cours ; `disabled` : webhooks désactivés, rien chez Sonarr/Radarr. */
export type WebhookState = "connected" | "pending" | "error" | "no_address" | "removing" | "disabled"

export type SourceState = "active" | "waiting" | "error"

export interface WebhookRead {
  service: ArrService
  /** 0 = instance principale. */
  instance_id: number
  name: string
  state: WebhookState
  error: string | null
  url: string | null
  /** Suivi en lecture seule de l'instance (historique, file d'attente) ;
   * null quand le temps réel est désactivé. */
  polling: SourceState | null
  polling_error: string | null
}

export interface WebhooksRead {
  analysarr_url: string
  realtime_enabled: boolean
  /** Création automatique des webhooks autorisée. */
  enabled: boolean
  webhooks: WebhookRead[]
}

export interface SourceStatus {
  /** `webhook:sonarr:0`, `arr:sonarr:0`, `torrents`, `media_server`, `requests`. */
  key: string
  kind: "webhook" | "arr" | "torrents" | "media_server" | "requests"
  state: SourceState
  last_event_at: string | null
  last_check_at: string | null
  error: string | null
}

export interface RealtimeStatus {
  /** Temps réel activé (sinon : scans uniquement). */
  enabled: boolean
  /** Activé ET au moins un service suivi. */
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
