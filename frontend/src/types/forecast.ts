/** Prévisions d'espace disque (backend : services/forecast.py). Jours au
 * format « AAAA-MM-JJ » (dates de calendrier, sans heure ni fuseau). */

/** Croissance en octets par jour, estimation centrale et fourchette. */
export interface Trend {
  per_day: number
  low: number
  high: number
}

/** Plein au plus tôt / au plus tard (null : pas de date au plus tard). */
export interface FillEstimate {
  earliest_days: number
  latest_days: number | null
  earliest_date: string
  latest_date: string | null
}

export interface DiskPoint {
  day: string
  used: number
  total: number
}

export interface ProjectionPoint {
  day: string
  used: number
  low: number
  high: number
}

export type DiskRole = "library" | "downloads"

export interface DiskForecast {
  /** Racines portées par ce disque, jointes par « + ». */
  key: string
  roles: DiskRole[]
  paths: string[]
  available: boolean
  total: number | null
  used: number | null
  free: number | null
  history: DiskPoint[]
  history_days: number
  trend: Trend | null
  fill: FillEstimate | null
  projection: ProjectionPoint[]
}

export interface Forecast {
  window_days: number
  min_history_days: number
  horizon_days: number
  history_days: number
  enough_history: boolean
  latest_day: string | null
  library: { size: number; trend: Trend | null } | null
  disks: DiskForecast[]
}
