/** Corbeille : une ligne par SUPPRESSION, restaurée d'un bloc
 * (backend : services/trash.py). */
export interface TrashItem {
  kind: "library_file" | "torrent"
  label: string
  size: number
  original_path: string | null
  /** Faux si l'élément a disparu de la corbeille (retiré à la main). */
  available: boolean
}

export interface TrashAction {
  id: number
  created_at: string
  action: string
  media_title: string
  media_type: string | null
  size: number
  items: TrashItem[]
  restores_arr: boolean
  /** Faux dès qu'un élément manque : la restauration serait incomplète. */
  restorable: boolean
}

export interface TrashStep {
  kind: string
  label: string
  success: boolean
  error: string | null
}

export interface TrashRestoreResult {
  steps: TrashStep[]
  complete: boolean
  /** Une analyse a été lancée pour faire réapparaître le média restauré. */
  rescan_started: boolean
  actions: TrashAction[]
}

export interface TrashSettings {
  enabled: boolean
  retention_days: number
  min_days: number
  max_days: number
}
