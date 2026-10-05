/** Assistant de nettoyage (backend : services/cleanup.py, cleanup_score.py). */
import type { SeedObligation } from "@/types/seed"

export type CleanupPreset = "prudent" | "balanced" | "space_first"
export type ComponentKey = "disinterest" | "potential" | "age" | "series"

export interface CleanupWeights {
  disinterest: number
  potential: number
  age: number
  series: number
}

export interface CleanupSettings {
  /** Préréglage de départ ; null = réglages personnalisés. */
  preset: CleanupPreset | null
  disinterest_days: number
  age_days: number
  inactive_days: number
  recent_days: number
  space_priority: number
  weights: CleanupWeights
}

export interface CleanupSettingsRead {
  settings: CleanupSettings
  presets: Record<CleanupPreset, CleanupSettings>
}

export type ProtectionKind = "favorite" | "request" | "request_unknown" | "recent" | "seed" | "excluded"

export interface Protection {
  kind: ProtectionKind
  names: string[]
  days: number | null
  obligation: SeedObligation | null
}

export interface ScoreComponent {
  key: ComponentKey
  value: number
  /** Part effective en %, après redistribution. */
  weight: number
  contribution: number
  days: number | null
  since: "last_played" | "added" | null
  users: number | null
  unfinished: number | null
  series_status: string | null
}

export interface CleanupCandidate {
  media_id: number
  media_type: "movie" | "series"
  title: string
  year: number | null
  has_poster: boolean
  poster_image_tag: string | null
  arr_instance_name: string | null
  score: number
  rank: number
  /** Espace réellement libéré en supprimant tout le média. */
  reclaimable_bytes: number
  main_reason: ComponentKey | null
  malus_in_progress: boolean
  protections: Protection[]
  last_played_at: string | null
  date_added: string | null
  series_status: string | null
  active_users: number
}

export interface CleanupCandidateDetail extends CleanupCandidate {
  components: ScoreComponent[]
  raw_score: number
  in_progress_users: string[]
}

export interface CleanupCandidatesPage {
  items: CleanupCandidate[]
  total: number
  candidate_count: number
  protected_count: number
  total_reclaimable_bytes: number
}

export type CleanupSort = "rank" | "score" | "space" | "title"

export interface CleanupQuery {
  page: number
  page_size: number
  sort: CleanupSort
  media_type?: "movie" | "series"
  search?: string
  include_protected: boolean
}

/** Mode objectif (backend : services/cleanup_plan.py) : simulation sans effet. */
export type CleanupPlanRequest =
  | { goal: "free"; target_bytes: number }
  | { goal: "until"; until: string; disk: string }

export interface PlanLossMedia {
  media_id: number
  title: string
  media_type: "movie" | "series"
  in_progress: boolean
  favorite: boolean
  /** Films : pourcentage de lecture. Séries : épisodes vus. */
  progress: number
}

/** Un compte et ce qu'il perdrait (médias commencés ou en favori). */
export interface PlanLoss {
  user_id: string
  name: string
  image_tag: string | null
  media: PlanLossMedia[]
}

export interface CleanupPlan {
  goal: "free" | "until"
  /** Espace à libérer (0 : le disque tient déjà jusqu'à la date). */
  target_bytes: number
  freed_bytes: number
  shortfall_bytes: number
  limited: boolean
  max_items: number
  items: CleanupCandidate[]
  losses: PlanLoss[]
  disk: string | null
  free_bytes: number | null
  days: number | null
  growth_per_day: number | null
}
