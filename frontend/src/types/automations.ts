export const AUTOMATION_TRIGGERS = [
  "orphan_detected",
  "duplicate_detected",
  "non_hardlink_detected",
  "import_failed_detected",
  "stalled_download_detected",
] as const
export const AUTOMATION_ACTIONS = [
  "cleanup",
  "repair_hardlinks",
  "cross_seed_search",
  "retry_import",
  "notify_only",
] as const

/** Conditions qui ont un sens pour chaque déclencheur : un import bloqué n'a
 * ni temps de seed ni ratio, un doublon n'a pas de torrent. Même table côté
 * backend (`services/automations.py::TRIGGER_CONDITIONS`), qui efface les
 * autres à l'enregistrement. */
export const CONDITIONS_BY_TRIGGER = {
  orphan_detected: ["min_seed_days", "min_ratio", "min_reclaimable_bytes"],
  duplicate_detected: ["min_reclaimable_bytes"],
  non_hardlink_detected: ["min_seed_days", "min_ratio"],
  import_failed_detected: [],
  stalled_download_detected: [],
} as const satisfies Record<(typeof AUTOMATION_TRIGGERS)[number], readonly string[]>

export type AutomationTrigger = (typeof AUTOMATION_TRIGGERS)[number]
export type AutomationAction = (typeof AUTOMATION_ACTIONS)[number]

export interface AutomationConditions {
  // Vide = films et séries.
  media_types: ("movie" | "series")[]
  min_seed_days?: number | null
  min_ratio?: number | null
  min_reclaimable_bytes?: number | null
}

export interface Automation {
  id: number
  name: string
  enabled: boolean
  trigger: AutomationTrigger
  action: AutomationAction
  conditions: AutomationConditions
  max_actions: number
  dry_run: boolean
  last_run_at: string | null
  last_run_count: number
}

/** Garde-fou : part de la bibliothèque qui peut basculer d'un scan à l'autre
 * avant que les règles ne soient suspendues (backend : automation_guard.py). */
export interface AutomationGuard {
  percent: number
  min_percent: number
  /** Faux si aucune automatisation activée ne porte sur les orphelins, les
   * doublons ou les torrents non hardlinkés : rien à protéger, rien à afficher. */
  active: boolean
  paused: boolean
  paused_at: string | null
  status: string | null
  previous: number | null
  current: number | null
  total: number | null
  changed_percent: number | null
}

export interface AutomationWrite {
  name: string
  enabled: boolean
  trigger: AutomationTrigger
  action: AutomationAction
  conditions: AutomationConditions
  max_actions: number
  dry_run: boolean
}

export interface AutomationStep {
  label: string
  success: boolean
  error: string | null
}

export interface AutomationRunResult {
  automation_id: number
  name: string
  matched: number
  executed: number
  dry_run: boolean
  freed_bytes: number
  steps: AutomationStep[]
}
