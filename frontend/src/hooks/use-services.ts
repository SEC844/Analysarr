import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { getServicesStatus } from "@/lib/api"

export const SERVICES_STATUS_QUERY_KEY = ["services", "status"] as const

// Le backend garde le résultat 60 s : relire régulièrement ne multiplie pas
// les requêtes vers les services.
export function useServicesStatusQuery() {
  return useQuery({
    queryKey: SERVICES_STATUS_QUERY_KEY,
    queryFn: () => getServicesStatus(),
    staleTime: 60 * 1000,
    refetchInterval: 2 * 60 * 1000,
  })
}

export function useRefreshServicesStatusMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => getServicesStatus(true),
    onSuccess: (data) => queryClient.setQueryData(SERVICES_STATUS_QUERY_KEY, data),
  })
}
