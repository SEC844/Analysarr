import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  getRealtimeSettings,
  getRealtimeStatus,
  previewWebhook,
  registerWebhook,
  saveRealtimeSettings,
  testWebhook,
  unregisterWebhook,
  scheduleNightlyScan,
} from "@/lib/api"
import { SETTINGS_QUERY_KEY } from "@/hooks/use-settings"
import type { RealtimeSettings, RealtimeSettingsWrite } from "@/types/realtime"

export const REALTIME_QUERY_KEY = ["realtime"] as const
const SETTINGS_KEY = [...REALTIME_QUERY_KEY, "settings"] as const

export function useRealtimeSettingsQuery() {
  return useQuery({ queryKey: SETTINGS_KEY, queryFn: getRealtimeSettings })
}

/** État des sources : relu toutes les 15 s, et aussitôt qu'un événement
 * `realtime.status` arrive par le flux (voir use-live-events). */
export function useRealtimeStatusQuery() {
  return useQuery({
    queryKey: [...REALTIME_QUERY_KEY, "status"],
    queryFn: getRealtimeStatus,
    refetchInterval: 15_000,
  })
}

function useStoreSettings() {
  const queryClient = useQueryClient()
  return (data: RealtimeSettings) => {
    queryClient.setQueryData(SETTINGS_KEY, data)
    queryClient.invalidateQueries({ queryKey: [...REALTIME_QUERY_KEY, "status"] })
  }
}

export function useSaveRealtimeSettingsMutation() {
  const store = useStoreSettings()
  return useMutation({
    mutationFn: (payload: RealtimeSettingsWrite) => saveRealtimeSettings(payload),
    onSuccess: store,
  })
}

interface WebhookTarget {
  service: string
  instanceId: number
}

export function usePreviewWebhookMutation() {
  return useMutation({
    mutationFn: ({ service, instanceId, url }: WebhookTarget & { url: string }) =>
      previewWebhook(service, instanceId, url),
  })
}

export function useRegisterWebhookMutation() {
  const store = useStoreSettings()
  return useMutation({
    mutationFn: ({ service, instanceId, url }: WebhookTarget & { url: string }) =>
      registerWebhook(service, instanceId, url),
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

export function useUnregisterWebhookMutation() {
  const store = useStoreSettings()
  return useMutation({
    mutationFn: ({ service, instanceId }: WebhookTarget) => unregisterWebhook(service, instanceId),
    onSuccess: store,
  })
}

export function useNightlyScanMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (hour: number) => scheduleNightlyScan(hour),
    onSuccess: (status) => {
      queryClient.setQueryData([...REALTIME_QUERY_KEY, "status"], status)
      // La planification affichée dans Réglages → Planification a changé.
      queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY })
    },
  })
}
