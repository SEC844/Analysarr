import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  crossSeedSearch,
  deleteExecute,
  deletePreview,
  getMedia,
  hardlinkRepairExecute,
  hardlinkRepairPreview,
  listMedia,
} from "@/lib/api"
import type { MediaListParams } from "@/types/media"

export function useMediaListQuery(params: MediaListParams) {
  return useQuery({
    queryKey: ["media", params],
    queryFn: () => listMedia(params),
  })
}

export function useMediaDetailQuery(id: number) {
  return useQuery({
    queryKey: ["media", "detail", id],
    queryFn: () => getMedia(id),
  })
}

export function useDeletePreviewMutation() {
  return useMutation({ mutationFn: (id: number) => deletePreview(id) })
}

export function useDeleteExecuteMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => deleteExecute(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ["media"] })
      queryClient.invalidateQueries({ queryKey: ["media", "detail", id] })
    },
  })
}

export function useCrossSeedSearchMutation() {
  return useMutation({ mutationFn: (id: number) => crossSeedSearch(id) })
}

export function useHardlinkRepairPreviewMutation() {
  return useMutation({ mutationFn: (id: number) => hardlinkRepairPreview(id) })
}

export function useHardlinkRepairExecuteMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => hardlinkRepairExecute(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ["media"] })
      queryClient.invalidateQueries({ queryKey: ["media", "detail", id] })
    },
  })
}
