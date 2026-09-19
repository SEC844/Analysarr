export const AUTOMATION_TRIGGERS = [
  "orphan_detected",
  "duplicate_detected",
  "non_hardlink_detected",
  "import_failed_detected",
] as const
export const AUTOMATION_ACTIONS = [
  "cleanup",
  "repair_hardlinks",
  "cross_seed_search",
  "retry_import",
  "notify_only",
] as const

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
