import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  createNotificationChannel,
  deleteNotificationChannel,
  listNotificationChannels,
  testNotificationChannel,
  updateNotificationChannel,
} from "@/lib/api"
import type { NotificationChannelWrite } from "@/types/notifications"

const CHANNELS_QUERY_KEY = ["notification-channels"] as const

export function useNotificationChannelsQuery() {
  return useQuery({ queryKey: CHANNELS_QUERY_KEY, queryFn: listNotificationChannels })
}

function useChannelsInvalidation() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: CHANNELS_QUERY_KEY })
}

export function useCreateChannelMutation() {
  const invalidate = useChannelsInvalidation()
  return useMutation({ mutationFn: (payload: NotificationChannelWrite) => createNotificationChannel(payload), onSuccess: invalidate })
}

export function useUpdateChannelMutation() {
  const invalidate = useChannelsInvalidation()
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: NotificationChannelWrite }) =>
      updateNotificationChannel(id, payload),
    onSuccess: invalidate,
  })
}

export function useDeleteChannelMutation() {
  const invalidate = useChannelsInvalidation()
  return useMutation({ mutationFn: (id: number) => deleteNotificationChannel(id), onSuccess: invalidate })
}

export function useTestChannelMutation() {
  return useMutation({ mutationFn: (id: number) => testNotificationChannel(id) })
}
