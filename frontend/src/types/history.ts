export type ActionKind = "delete_selection" | "cascade_delete" | "hardlink_repair" | "cross_seed_search"

export interface ActionStep {
  label: string
  success: boolean
  error: string | null
}

export interface ActionLogEntry {
  id: number
  created_at: string
  action: ActionKind
  media_title: string
  media_type: string | null
  // null si la fiche média n'existe plus (supprimée ou rescannée).
  media_id: number | null
  success_count: number
  failure_count: number
  freed_bytes: number | null
  details: ActionStep[]
}
