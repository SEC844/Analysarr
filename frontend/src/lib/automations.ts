import type { AutomationAction, AutomationWrite } from "@/types/automations"

/** Planchers et plafond de la suppression de médias entiers (même valeurs
 * que `services/automations.py`, qui revérifie tout). */
export const MIN_CLEANUP_SCORE = 50
export const MIN_CLEANUP_GIB = 1
export const MAX_MEDIA_DELETIONS = 10
export const MAX_ACTIONS = 50
export const GIGABYTE = 1024 ** 3

/** Valeurs proposées en passant au déclencheur « candidat au nettoyage » :
 * prudentes, à ajuster. */
export const CLEANUP_DEFAULTS = { min_score: 80, min_reclaimable_bytes: 10 * GIGABYTE }

export function maxActionsFor(action: AutomationAction): number {
  return action === "delete_media" ? MAX_MEDIA_DELETIONS : MAX_ACTIONS
}

/** Règle « candidat au nettoyage » incomplète : score ET espace obligatoires,
 * au-dessus des planchers. */
export function cleanupRuleInvalid(rule: AutomationWrite): boolean {
  if (rule.trigger !== "cleanup_candidate") return false
  const { min_score, min_reclaimable_bytes } = rule.conditions
  return (
    min_score == null ||
    min_score < MIN_CLEANUP_SCORE ||
    min_score > 100 ||
    min_reclaimable_bytes == null ||
    min_reclaimable_bytes < MIN_CLEANUP_GIB * GIGABYTE
  )
}
