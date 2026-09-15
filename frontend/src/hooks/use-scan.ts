import { useCallback, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { useI18n } from "@/i18n"
import { getScanHistory, startScan } from "@/lib/api"
import type { ScanEvent } from "@/types/media"

export function useScanHistoryQuery() {
  return useQuery({
    queryKey: ["scan", "history"],
    queryFn: () => getScanHistory(),
  })
}

export function useScanRunner() {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [event, setEvent] = useState<ScanEvent | null>(null)
  const [isRunning, setIsRunning] = useState(false)
  const sourceRef = useRef<EventSource | null>(null)

  const start = useCallback(() => {
    if (sourceRef.current) return

    // On s'abonne au flux SSE AVANT de déclencher le scan, pour ne pas
    // rater les premiers événements si le scan démarre plus vite que la
    // connexion EventSource ne s'établit.
    const source = new EventSource("/api/scan/stream")
    sourceRef.current = source
    setIsRunning(true)
    setEvent({ type: "started" })

    let triggered = false
    let fallbackTimer: ReturnType<typeof setTimeout> | undefined

    const cleanup = () => {
      clearTimeout(fallbackTimer)
      source.close()
      sourceRef.current = null
      setIsRunning(false)
    }

    const trigger = () => {
      if (triggered) return
      triggered = true
      clearTimeout(fallbackTimer)
      startScan()
        .then((result) => {
          if (!result.started) {
            setEvent({ type: "failed", message: result.message ?? t("scan.alreadyRunning") })
            cleanup()
          }
        })
        .catch((error: unknown) => {
          setEvent({ type: "failed", message: error instanceof Error ? error.message : String(error) })
          cleanup()
        })
    }

    source.onmessage = (e) => {
      const data = JSON.parse(e.data) as ScanEvent
      setEvent(data)
      if (data.type === "completed" || data.type === "failed") {
        cleanup()
        queryClient.invalidateQueries({ queryKey: ["media"] })
      }
    }
    source.onerror = () => {
      cleanup()
    }

    source.onopen = trigger
    // Filet de sécurité : si l'ouverture du flux n'est jamais signalée (proxy
    // qui met la réponse en tampon), le scan est lancé quand même plutôt que
    // de rester indéfiniment sur « Démarrage du scan... ».
    fallbackTimer = setTimeout(trigger, 3000)
  }, [queryClient, t])

  return { start, isRunning, event }
}
