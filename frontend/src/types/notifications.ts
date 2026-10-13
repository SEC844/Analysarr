export type ChannelKind = "discord" | "ntfy" | "gotify"

// Même liste que le backend (services/notifications.NOTIFICATION_EVENTS).
export const NOTIFICATION_EVENTS = [
  "scan_completed",
  "scan_failed",
  "orphan_detected",
  "duplicate_detected",
  "non_hardlink_detected",
  "import_failed_detected",
  "stalled_download_detected",
  "untracked_detected",
  "delete_selection",
  "import_retry",
  "cascade_delete",
  "hardlink_repair",
  "cross_seed_search",
  "arr_link",
  "automation",
  "update_available",
  "automations_paused",
  "weekly_summary",
] as const

export type NotificationEvent = (typeof NOTIFICATION_EVENTS)[number]

export interface NotificationChannel {
  id: number
  kind: ChannelKind
  name: string
  enabled: boolean
  events: NotificationEvent[]
  // Renseignée uniquement pour Gotify : les adresses Discord et ntfy sont des secrets.
  url: string | null
  url_set: boolean
  token_set: boolean
}

export interface NotificationChannelWrite {
  kind: ChannelKind
  name: string
  enabled: boolean
  events: NotificationEvent[]
  // Vide à la modification : conserve l'adresse enregistrée.
  url?: string
  token?: string
}

export interface ChannelTestResult {
  ok: boolean
  error: string | null
}

/** Envoi immédiat du résumé hebdomadaire (backend : services/weekly_summary.py).
 * `skipped` : rien n'a été envoyé. */
export interface WeeklySummaryResult {
  channels: { name: string; error: string | null }[]
  skipped: "no_subscriber" | "library_unknown" | null
}
