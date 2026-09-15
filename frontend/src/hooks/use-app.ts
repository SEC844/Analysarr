import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { checkUpdates, getAppInfo, saveAppPreferences } from "@/lib/api"
import type { AppPreferences } from "@/types/app"

export const APP_INFO_QUERY_KEY = ["app", "info"] as const

// Le backend garde le résultat de la vérification en cache (6 h) : relire
// l'état régulièrement ne déclenche pas de requête vers GitHub à chaque fois.
export function useAppInfoQuery(enabled = true) {
  return useQuery({
    queryKey: APP_INFO_QUERY_KEY,
    queryFn: getAppInfo,
    enabled,
    staleTime: 30 * 60 * 1000,
    refetchInterval: 60 * 60 * 1000,
  })
}

export function useCheckUpdatesMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: checkUpdates,
    onSuccess: (data) => queryClient.setQueryData(APP_INFO_QUERY_KEY, data),
  })
}

export function useSaveAppPreferencesMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: AppPreferences) => saveAppPreferences(payload),
    onSuccess: (data) => queryClient.setQueryData(APP_INFO_QUERY_KEY, data),
  })
}
