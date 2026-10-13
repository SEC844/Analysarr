import { useCallback, useContext, useSyncExternalStore } from "react"

import { RecentlyUpdatedContext } from "@/lib/live-context"

/** Vrai quelques secondes après une mise à jour en temps réel du média. */
export function useRecentlyUpdated(mediaId: number): boolean {
  const store = useContext(RecentlyUpdatedContext)
  const snapshot = useCallback(() => store.has(mediaId), [store, mediaId])
  return useSyncExternalStore(store.subscribe, snapshot)
}
