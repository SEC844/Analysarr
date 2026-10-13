/** Protection du seed (backend : services/seed_protection.py). */
export interface TrackerSeedRule {
  domain: string
  min_days: number
  /** Ratio exigé EN PLUS de la durée ; null = aucun. */
  min_ratio: number | null
}

export interface SeedProtectionWrite {
  enabled: boolean
  private_min_days: number
  public_enabled: boolean
  public_min_days: number
  tracker_rules: TrackerSeedRule[]
}

export interface SeedProtection extends SeedProtectionWrite {
  /** Bandeau proposé à une installation existante. */
  prompt: boolean
  /** Trackers vus au dernier scan, proposés à la saisie. */
  known_trackers: string[]
  min_days: number
  max_days: number
  max_ratio: number
  max_rules: number
}

/** Obligation de seed en cours : jusqu'à `until` (min_seed), ratio pas encore
 * atteint (min_ratio), ou aucune date de référence (unknown_date). */
export interface SeedObligation {
  reason: "min_seed" | "min_ratio" | "unknown_date"
  until: string | null
  tracker: string | null
  min_days: number
  min_ratio: number | null
}
