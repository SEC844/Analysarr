/** Corbeille : fichiers déplacés par Analysarr au lieu d'être supprimés
 * (backend : services/trash.py). */
export interface TrashEntry {
  id: number
  deleted_at: string
  original_path: string
  size: number
  media_title: string
  action: string
  /** Faux si le fichier a été retiré de la corbeille à la main. */
  available: boolean
}

export interface TrashSettings {
  enabled: boolean
  retention_days: number
  min_days: number
  max_days: number
}
