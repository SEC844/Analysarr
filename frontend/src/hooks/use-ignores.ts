import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { createIgnore, deleteIgnore, listIgnores } from "@/lib/api"
import type { IgnoreCreate } from "@/types/ignores"

const IGNORES_QUERY_KEY = ["ignores"] as const

export function useIgnoresQuery() {
  return useQuery({ queryKey: IGNORES_QUERY_KEY, queryFn: listIgnores })
}

/** Ignorer ou ne plus ignorer change les statuts du média sur-le-champ : la
 * fiche, la bibliothèque, la liste des éléments ignorés et l'historique sont
 * relus. */
function useIgnoreInvalidation() {
  const queryClient = useQueryClient()
  return () => {
    queryClient.invalidateQueries({ queryKey: IGNORES_QUERY_KEY })
    queryClient.invalidateQueries({ queryKey: ["media"] })
    queryClient.invalidateQueries({ queryKey: ["history"] })
  }
}

export function useCreateIgnoreMutation() {
  const invalidate = useIgnoreInvalidation()
  return useMutation({ mutationFn: (payload: IgnoreCreate) => createIgnore(payload), onSuccess: invalidate })
}

export function useDeleteIgnoreMutation() {
  const invalidate = useIgnoreInvalidation()
  return useMutation({ mutationFn: (id: number) => deleteIgnore(id), onSuccess: invalidate })
}
