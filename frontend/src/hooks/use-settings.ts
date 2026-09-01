import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { getSettings, saveSettings } from "@/lib/api"
import type { SettingsWrite } from "@/types/settings"

export const SETTINGS_QUERY_KEY = ["settings"] as const

export function useSettingsQuery() {
  return useQuery({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: getSettings,
  })
}

export function useSaveSettingsMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: SettingsWrite) => saveSettings(payload),
    onSuccess: (data) => {
      queryClient.setQueryData(SETTINGS_QUERY_KEY, data)
    },
  })
}
