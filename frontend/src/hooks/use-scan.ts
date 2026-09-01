import { useCallback, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"

import { startScan } from "@/lib/api"
import type { ScanEvent } from "@/types/media"

export function useScanRunner() {
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

    const cleanup = () => {
      source.close()
      sourceRef.current = null
      setIsRunning(false)
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

    source.onopen = () => {
      startScan().then((result) => {
        if (!result.started) {
          setEvent({ type: "failed", message: result.message ?? "Un scan est déjà en cours." })
          cleanup()
        }
      })
    }
  }, [queryClient])

  return { start, isRunning, event }
}
