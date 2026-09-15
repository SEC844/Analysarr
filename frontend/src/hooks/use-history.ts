import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { clearActionHistory, getActionHistory, testNotifications } from "@/lib/api"

const HISTORY_QUERY_KEY = ["history"] as const

export function useActionHistoryQuery() {
  return useQuery({
    queryKey: HISTORY_QUERY_KEY,
    queryFn: () => getActionHistory(),
  })
}

export function useClearActionHistoryMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: clearActionHistory,
    onSuccess: () => queryClient.setQueryData(HISTORY_QUERY_KEY, []),
  })
}

export function useTestNotificationsMutation() {
  return useMutation({ mutationFn: testNotifications })
}
