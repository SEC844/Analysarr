import type { LiveEvent } from "@/types/realtime"

/** Ce qu'une rafale d'événements oblige à relire. Regroupé : un pack de
 * saison importé épisode par épisode ne relit la bibliothèque qu'une fois. */
export interface InvalidationPlan {
  /** Listes de la bibliothèque (accueil, filtres). */
  lists: boolean
  /** Fiches à relire (et à surligner). */
  details: number[]
  /** Visionnage seul à relire. */
  watches: number[]
  /** Fiches disparues : retirées du cache. */
  removed: number[]
  cleanup: boolean
  realtime: boolean
  scans: boolean
}

export function planInvalidation(events: LiveEvent[]): InvalidationPlan {
  const details = new Set<number>()
  const watches = new Set<number>()
  const removed = new Set<number>()
  const plan: InvalidationPlan = {
    lists: false,
    details: [],
    watches: [],
    removed: [],
    cleanup: false,
    realtime: false,
    scans: false,
  }
  for (const event of events) {
    switch (event.type) {
      case "media.updated":
        plan.lists = true
        if (event.watch) watches.add(event.media_id)
        else details.add(event.media_id)
        break
      case "media.removed":
        plan.lists = plan.cleanup = true
        removed.add(event.media_id)
        break
      case "library.changed":
        plan.lists = plan.cleanup = plan.scans = true
        break
      case "cleanup.changed":
        plan.cleanup = true
        break
      case "realtime.status":
        plan.realtime = true
        break
      case "resync":
        // Événements manqués (reconnexion, retard) : tout est relu.
        return { lists: true, details: [], watches: [], removed: [], cleanup: true, realtime: true, scans: true }
    }
  }
  plan.details = [...details].filter((id) => !removed.has(id))
  plan.watches = [...watches].filter((id) => !removed.has(id) && !details.has(id))
  plan.removed = [...removed]
  return plan
}

export function parseLiveEvent(raw: string): LiveEvent | null {
  try {
    const event: unknown = JSON.parse(raw)
    if (typeof event !== "object" || event === null || typeof (event as { type?: unknown }).type !== "string") {
      return null
    }
    return event as LiveEvent
  } catch {
    return null
  }
}
