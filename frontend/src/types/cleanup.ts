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
  /** `undated` : vu par au moins un compte, sans date de lecture connue. */
  since: "last_played" | "added" | "undated" | null
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

/** Espace sur le disque (chaque fichier physique une fois) contre espace
 * réellement libéré : la différence est retenue par des liens hors du média. */
export interface CleanupSpace {
  on_disk_bytes: number
  freed_bytes: number
  held_bytes: number
  external_links: number
}

/** Autres liens trouvés sous `roots` ; `complete` faux si la recherche s'est
 * arrêtée avant la fin (temps ou nombre de résultats). */
export interface OtherLinks {
  paths: string[]
  complete: boolean
  roots: string[]
}

export interface CleanupCandidateDetail extends CleanupCandidate {
  components: ScoreComponent[]
  space: CleanupSpace
  raw_score: number
  in_progress_users: string[]
}

export interface CleanupCandidatesPage {
  items: CleanupCandidate[]
  total: number
  candidate_count: number
  protected_count: number
  total_reclaimable_bytes: number
  /** Sources illisibles au dernier scan complet : scores et protections faussés. */
  unreliable_sources: ("watch" | "requests")[]
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
