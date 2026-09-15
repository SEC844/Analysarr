import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  crossSeedSearch,
  deleteExecute,
  deletePreview,
  deleteSelectionExecute,
  getDeleteFootprint,
  getMedia,
  getMediaWatch,
  hardlinkRepairExecute,
  hardlinkRepairPreview,
  listEmbyUsers,
  listMedia,
  type CrossSeedSearchScope,
} from "@/lib/api"
import type { MediaDeleteSelection, MediaListParams } from "@/types/media"

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

// Rafraîchi en direct côté backend (appels Emby) : une minute de fraîcheur
// suffit, la fiche et la fenêtre de suppression partagent le même résultat.
export function useMediaWatchQuery(id: number, enabled = true) {
  return useQuery({
    queryKey: ["media", "watch", id],
    queryFn: () => getMediaWatch(id),
    enabled,
    staleTime: 60 * 1000,
  })
}

export function useEmbyUsersQuery(enabled = true) {
  return useQuery({ queryKey: ["emby", "users"], queryFn: listEmbyUsers, enabled })
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

// Sous la clé "media" : invalidée avec le reste après chaque suppression,
// l'empreinte disque est donc toujours recalculée à la réouverture.
export function useDeleteFootprintQuery(id: number, enabled: boolean) {
  return useQuery({
    queryKey: ["media", "delete-footprint", id],
    queryFn: () => getDeleteFootprint(id),
    enabled,
  })
}

export function useDeleteSelectionExecuteMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, selection }: { id: number; selection: MediaDeleteSelection }) =>
      deleteSelectionExecute(id, selection),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["media"] })
      queryClient.invalidateQueries({ queryKey: ["media", "detail", id] })
    },
  })
}

export function useCrossSeedSearchMutation() {
  return useMutation({
    mutationFn: ({ id, scope }: { id: number; scope?: CrossSeedSearchScope }) => crossSeedSearch(id, scope),
  })
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
