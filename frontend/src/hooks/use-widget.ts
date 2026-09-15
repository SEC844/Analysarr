import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { createWidgetKey, getWidgetKey, revokeWidgetKey } from "@/lib/api"

const WIDGET_KEY_QUERY_KEY = ["widget-key"] as const

export function useWidgetKeyQuery() {
  return useQuery({ queryKey: WIDGET_KEY_QUERY_KEY, queryFn: getWidgetKey })
}

export function useCreateWidgetKeyMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: createWidgetKey,
    // La clé elle-même n'est jamais mise en cache : seul l'état est conservé.
    onSuccess: () => queryClient.setQueryData(WIDGET_KEY_QUERY_KEY, { enabled: true, key: null }),
  })
}

export function useRevokeWidgetKeyMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: revokeWidgetKey,
    onSuccess: (data) => queryClient.setQueryData(WIDGET_KEY_QUERY_KEY, data),
  })
}
