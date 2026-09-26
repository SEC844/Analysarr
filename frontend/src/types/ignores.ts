import type { MediaStatus } from "@/types/media"

export type IgnoreKind = "torrent" | "file" | "status"

/** Ce qu'on ignore est toujours désigné par un identifiant du média : le
 * serveur revérifie qu'il déclenche bien une alerte (jamais un hash ou un
 * chemin fourni par l'interface). */
export interface IgnoreCreate {
  media_id: number
  kind: IgnoreKind
  torrent_id?: number
  file_id?: number
  statuses?: MediaStatus[]
  note?: string
}

export interface IgnoreRuleRead {
  id: number
  kind: IgnoreKind
  // Nom du torrent, chemin du fichier ; statut masqué pour une alerte.
  label: string
  status: MediaStatus | null
  media_title: string
  media_type: string
  // null si le média n'existe plus.
  media_id: number | null
  present: boolean
  note: string | null
  created_at: string
}

export interface MutedStatusRead {
  status: MediaStatus
  rule_id: number
  note: string | null
}
