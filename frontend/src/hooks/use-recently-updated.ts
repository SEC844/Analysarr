import { useContext } from "react"

import { RecentlyUpdatedContext } from "@/lib/live-context"

/** Vrai quelques secondes après une mise à jour en temps réel du média. */
export function useRecentlyUpdated(mediaId: number): boolean {
  return useContext(RecentlyUpdatedContext).has(mediaId)
}
