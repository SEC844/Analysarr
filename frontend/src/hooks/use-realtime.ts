import { useEffect, useRef } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { getRealtimeStatus, getWebhooks, retryWebhook, saveAnalysarrAddress, testWebhook } from "@/lib/api"
import { detectedAddress } from "@/lib/realtime"
import type { WebhooksRead } from "@/types/realtime"

export const REALTIME_QUERY_KEY = ["realtime"] as const
const WEBHOOKS_KEY = [...REALTIME_QUERY_KEY, "webhooks"] as const

/** État des sources : relu toutes les 15 s, et aussitôt qu'un événement
 * `realtime.status` arrive par le flux (voir live-events). */
export function useRealtimeStatusQuery() {
  return useQuery({
    queryKey: [...REALTIME_QUERY_KEY, "status"],
    queryFn: getRealtimeStatus,
    refetchInterval: 15_000,
  })
}

/** Webhooks Sonarr/Radarr : relus toutes les 5 s tant que l'un d'eux est en
 * cours de branchement (le superviseur agit dans les secondes qui suivent). */
export function useWebhooksQuery() {
  return useQuery({
    queryKey: WEBHOOKS_KEY,
    queryFn: getWebhooks,
    refetchInterval: (query) =>
      query.state.data?.webhooks.some((w) => w.state === "pending") ? 5_000 : false,
  })
}

function useStoreWebhooks() {
  const queryClient = useQueryClient()
  return (data: WebhooksRead) => {
    queryClient.setQueryData(WEBHOOKS_KEY, data)
    queryClient.invalidateQueries({ queryKey: [...REALTIME_QUERY_KEY, "status"] })
  }
}

export function useSaveAddressMutation() {
  const store = useStoreWebhooks()
  return useMutation({
    mutationFn: ({ url, detected = false }: { url: string; detected?: boolean }) => saveAnalysarrAddress(url, detected),
    onSuccess: store,
  })
}

interface WebhookTarget {
  service: string
  instanceId: number
}

export function useRetryWebhookMutation() {
  const store = useStoreWebhooks()
  return useMutation({
    mutationFn: ({ service, instanceId }: WebhookTarget) => retryWebhook(service, instanceId),
    onSuccess: store,
  })
}

export function useTestWebhookMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ service, instanceId }: WebhookTarget) => testWebhook(service, instanceId),
    // L'événement de test arrive par le webhook : l'état de la source change.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [...REALTIME_QUERY_KEY, "status"] }),
  })
}

/** Première visite : l'adresse d'Analysarr (pour les webhooks) n'est pas
 * encore connue — celle de ce navigateur est proposée, une seule fois. Le
 * serveur ne remplace jamais une adresse déjà enregistrée. */
export function useDetectAnalysarrAddress() {
  const { data } = useRealtimeStatusQuery()
  const save = useSaveAddressMutation()
  const sent = useRef(false)
  const { mutate } = save
  useEffect(() => {
    if (!data || data.address_set || sent.current) return
    sent.current = true
    mutate({ url: detectedAddress(window.location), detected: true })
  }, [data, mutate])
}
