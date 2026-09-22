import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { checkUpdates, getAppInfo, saveAppPreferences } from "@/lib/api"
import { DEFAULT_UI_PREFERENCES, type AppInfo, type AppPreferences, type UiPreferences } from "@/types/app"

export const APP_INFO_QUERY_KEY = ["app", "info"] as const

// Le backend garde le résultat de la vérification GitHub en cache (10 min) et
// le rafraîchit en tâche de fond : relire l'état souvent est sans coût, et
// permet de repérer rapidement qu'une nouvelle version du conteneur vient
// d'être installée (voir App.tsx).
export function useAppInfoQuery(enabled = true) {
  return useQuery({
    queryKey: APP_INFO_QUERY_KEY,
    queryFn: getAppInfo,
    enabled,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 15 * 60 * 1000,
  })
}

/** Préférences d'affichage, LUES dans le cache de `useAppInfoQuery` sans
 * jamais déclencher l'appel : `/api/app/info` est protégé, et ce hook est
 * utilisé dès le premier rendu, avant même l'écran de connexion. Un appel émis
 * là répondait 401 — donc, avant correction, une redirection vers l'accueil qui
 * relançait le même appel, en boucle (écran clignotant derrière un
 * reverse-proxy, où aucun cookie de session n'existait encore). C'est App.tsx
 * qui lance l'unique requête, une fois l'authentification confirmée. */
export function usePreferences(): UiPreferences {
  const { data } = useQuery({ queryKey: APP_INFO_QUERY_KEY, queryFn: getAppInfo, enabled: false })
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
