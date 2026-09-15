import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { checkUpdates, getAppInfo, saveAppPreferences } from "@/lib/api"
import { DEFAULT_UI_PREFERENCES, type AppInfo, type AppPreferences, type UiPreferences } from "@/types/app"

export const APP_INFO_QUERY_KEY = ["app", "info"] as const

// Le backend garde le résultat de la vérification GitHub en cache (6 h) :
// relire l'état souvent est sans coût, et permet de repérer rapidement qu'une
// nouvelle version du conteneur vient d'être installée (voir App.tsx).
export function useAppInfoQuery(enabled = true) {
  return useQuery({
    queryKey: APP_INFO_QUERY_KEY,
    queryFn: getAppInfo,
    enabled,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 15 * 60 * 1000,
  })
}

export function usePreferences(): UiPreferences {
  const { data } = useAppInfoQuery()
  return data?.ui ?? DEFAULT_UI_PREFERENCES
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
    // Mise à jour immédiate de l'affichage, annulée si l'enregistrement échoue.
    onMutate: (payload) => {
      const previous = queryClient.getQueryData<AppInfo>(APP_INFO_QUERY_KEY)
      if (previous && payload.ui) queryClient.setQueryData(APP_INFO_QUERY_KEY, { ...previous, ui: payload.ui })
      return { previous }
    },
    onError: (_err, _payload, context) => {
      if (context?.previous) queryClient.setQueryData(APP_INFO_QUERY_KEY, context.previous)
    },
    onSuccess: (data) => queryClient.setQueryData(APP_INFO_QUERY_KEY, data),
  })
}
