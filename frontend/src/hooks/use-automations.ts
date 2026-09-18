import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { createAutomation, deleteAutomation, listAutomations, previewAutomation, runAutomation, updateAutomation } from "@/lib/api"
import type { AutomationWrite } from "@/types/automations"

const AUTOMATIONS_QUERY_KEY = ["automations"] as const
const HISTORY_QUERY_KEY = ["history"] as const

export function useAutomationsQuery() {
  return useQuery({ queryKey: AUTOMATIONS_QUERY_KEY, queryFn: listAutomations })
}

function useAutomationsInvalidation() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: AUTOMATIONS_QUERY_KEY })
}

export function useCreateAutomationMutation() {
  const invalidate = useAutomationsInvalidation()
  return useMutation({ mutationFn: (payload: AutomationWrite) => createAutomation(payload), onSuccess: invalidate })
}

export function useUpdateAutomationMutation() {
  const invalidate = useAutomationsInvalidation()
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: AutomationWrite }) => updateAutomation(id, payload),
    onSuccess: invalidate,
  })
}

export function useDeleteAutomationMutation() {
  const invalidate = useAutomationsInvalidation()
  return useMutation({ mutationFn: (id: number) => deleteAutomation(id), onSuccess: invalidate })
}

export function usePreviewAutomationMutation() {
  return useMutation({ mutationFn: (id: number) => previewAutomation(id) })
}

export function useRunAutomationMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => runAutomation(id),
    // Une exécution supprime ou répare : la bibliothèque et l'historique changent.
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AUTOMATIONS_QUERY_KEY })
      queryClient.invalidateQueries({ queryKey: HISTORY_QUERY_KEY })
      queryClient.invalidateQueries({ queryKey: ["media"] })
    },
  })
}
