import { useEffect, useState, type ReactNode } from "react"
import { useQueryClient, type QueryClient } from "@tanstack/react-query"

import { RecentlyUpdatedContext } from "@/lib/live-context"
import { parseLiveEvent, planInvalidation, type InvalidationPlan } from "@/lib/live-events"
import type { LiveEvent } from "@/types/realtime"

// Rafale d'événements regroupée avant de relire quoi que ce soit.
const FLUSH_DELAY_MS = 250
// Durée de la surbrillance d'un média modifié.
const HIGHLIGHT_MS = 4000

function apply(queryClient: QueryClient, plan: InvalidationPlan): void {
  if (plan.lists) {
    // Listes de la bibliothèque seulement (clé ["media", paramètres]) : jamais
    // un dialogue ouvert (empreinte disque, rattachement) en plein choix.
    queryClient.invalidateQueries({
      queryKey: ["media"],
      predicate: (query) => typeof query.queryKey[1] === "object" && query.queryKey[1] !== null,
    })
  }
  for (const id of plan.details) {
    queryClient.invalidateQueries({ queryKey: ["media", "detail", id] })
    queryClient.invalidateQueries({ queryKey: ["media", "watch", id] })
  }
  for (const id of plan.watches) {
    queryClient.invalidateQueries({ queryKey: ["media", "watch", id] })
  }
  for (const id of plan.removed) {
    queryClient.removeQueries({ queryKey: ["media", "detail", id] })
  }
  if (plan.cleanup) queryClient.invalidateQueries({ queryKey: ["cleanup"] })
  if (plan.realtime) queryClient.invalidateQueries({ queryKey: ["realtime"] })
  if (plan.scans) queryClient.invalidateQueries({ queryKey: ["scan", "history"] })
}

/** Abonnement unique, pour toute l'application, au flux des événements de la
 * bibliothèque : chaque changement relit ce qu'il touche, et le média modifié
 * est brièvement surligné. Le navigateur se reconnecte seul après une coupure ;
 * à la reconnexion, tout est relu (des événements ont pu être manqués). */
export function LiveEventsProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [recent, setRecent] = useState<ReadonlySet<number>>(new Set())

  useEffect(() => {
    if (typeof EventSource === "undefined") return
    const source = new EventSource("/api/events/stream")
    let pending: LiveEvent[] = []
    let flushTimer: ReturnType<typeof setTimeout> | undefined
    const highlightTimers = new Set<ReturnType<typeof setTimeout>>()
    let interrupted = false

    const flush = () => {
      flushTimer = undefined
      const plan = planInvalidation(pending)
      pending = []
      apply(queryClient, plan)
      const touched = [...plan.details, ...plan.watches]
      if (touched.length === 0) return
      setRecent((current) => new Set([...current, ...touched]))
      const timer = setTimeout(() => {
        highlightTimers.delete(timer)
        setRecent((current) => new Set([...current].filter((id) => !touched.includes(id))))
      }, HIGHLIGHT_MS)
      highlightTimers.add(timer)
    }
    const queue = (event: LiveEvent) => {
      pending.push(event)
      flushTimer ??= setTimeout(flush, FLUSH_DELAY_MS)
    }

    source.onmessage = (message: MessageEvent<string>) => {
      const event = parseLiveEvent(message.data)
      if (event) queue(event)
    }
    source.onerror = () => {
      interrupted = true
    }
    source.onopen = () => {
      if (interrupted) {
        interrupted = false
        queue({ type: "resync" })
      }
    }
    return () => {
      source.close()
      if (flushTimer) clearTimeout(flushTimer)
      for (const timer of highlightTimers) clearTimeout(timer)
    }
  }, [queryClient])

  return <RecentlyUpdatedContext.Provider value={recent}>{children}</RecentlyUpdatedContext.Provider>
}
