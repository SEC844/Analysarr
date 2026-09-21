import { useCallback, useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { useI18n } from "@/i18n"
import { getScanHistory, getScanStatus, startScan } from "@/lib/api"
import type { ScanEvent, ScanScope } from "@/types/media"

export function useScanHistoryQuery() {
  return useQuery({
    queryKey: ["scan", "history"],
    queryFn: () => getScanHistory(),
  })
}

/** État de l'analyse en cours, côté serveur. Consulté au montage : une analyse
 * lancée puis quittée (navigation, autre onglet, rechargement) doit rester
 * visible comme « en cours » quand on revient. */
function useServerScanStatus() {
  return useQuery({
    queryKey: ["scan", "status"],
    queryFn: () => getScanStatus(),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 3000 : false),
  })
}

export function useScanRunner() {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [event, setEvent] = useState<ScanEvent | null>(null)
  const [isStarting, setIsStarting] = useState(false)
  const sourceRef = useRef<EventSource | null>(null)
  const status = useServerScanStatus()

  const serverRunning = status.data?.status === "running"

  // Ouvre le flux d'événements. `onOpen` lance l'analyse quand c'est ce
  // composant qui la déclenche ; sans lui, on ne fait que suivre une analyse
  // déjà en cours.
  const openStream = useCallback(
    (onOpen?: () => void) => {
      if (sourceRef.current) return
      const source = new EventSource("/api/scan/stream")
      sourceRef.current = source
      setIsStarting(true)

      let fallbackTimer: ReturnType<typeof setTimeout> | undefined
      const cleanup = () => {
        clearTimeout(fallbackTimer)
        source.close()
        sourceRef.current = null
        setIsStarting(false)
        queryClient.invalidateQueries({ queryKey: ["scan", "status"] })
      }

      if (onOpen) {
        let triggered = false
        const trigger = () => {
          if (triggered) return
          triggered = true
          clearTimeout(fallbackTimer)
          onOpen()
        }
        source.onopen = trigger
        // Filet de sécurité : si l'ouverture du flux n'est jamais signalée
        // (proxy qui met la réponse en tampon), l'analyse part quand même
        // plutôt que de rester sur « Démarrage du scan... ».
        fallbackTimer = setTimeout(trigger, 3000)
      }

      source.onmessage = (e) => {
        const data = JSON.parse(e.data) as ScanEvent
        setEvent(data)
        if (data.type === "completed" || data.type === "failed") {
          cleanup()
          queryClient.invalidateQueries({ queryKey: ["media"] })
          queryClient.invalidateQueries({ queryKey: ["scan", "history"] })
        }
      }
      source.onerror = () => cleanup()
    },
    [queryClient],
  )

  const start = useCallback(
    (scope: ScanScope = "full") => {
      if (sourceRef.current) return
      setEvent({ type: "started" })
      openStream(() => {
        startScan(scope)
          .then((result) => {
            if (!result.started) {
              setEvent({ type: "failed", message: result.message ?? t("scan.alreadyRunning") })
              sourceRef.current?.close()
              sourceRef.current = null
              setIsStarting(false)
            }
          })
          .catch((error: unknown) => {
            setEvent({ type: "failed", message: error instanceof Error ? error.message : String(error) })
            sourceRef.current?.close()
            sourceRef.current = null
            setIsStarting(false)
          })
      })
    },
    [openStream, t],
  )

  // Analyse déjà en cours au montage (lancée depuis une autre page ou un autre
  // onglet) : on s'y raccroche pour afficher son avancement.
  useEffect(() => {
    if (serverRunning && !sourceRef.current) openStream()
  }, [serverRunning, openStream])

  useEffect(() => {
    return () => {
      sourceRef.current?.close()
      sourceRef.current = null
    }
  }, [])

  return { start, isRunning: isStarting || serverRunning, event }
}
