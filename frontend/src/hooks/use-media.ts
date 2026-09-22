import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  crossSeedSearch,
  deleteExecute,
  deletePreview,
  deleteSelectionExecute,
  getArrLinkPreview,
  getDeleteFootprint,
  getMedia,
  getMediaWatch,
  hardlinkRepairExecute,
  hardlinkRepairPreview,
  linkMediaToArr,
  listEmbyUsers,
  listMedia,
  rescanMedia,
  retryImport,
  type CrossSeedSearchScope,
} from "@/lib/api"
import type { ArrLinkRequest, MediaDeleteSelection, MediaListParams } from "@/types/media"

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

export function useRescanMediaMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => rescanMedia(id),
    onSuccess: (_data, id) => {
      // La fiche et la bibliothèque reflètent immédiatement l'analyse : les
      // statuts et les torrents viennent d'être recalculés côté serveur.
      queryClient.invalidateQueries({ queryKey: ["media", "detail", id] })
      queryClient.invalidateQueries({ queryKey: ["media"] })
    },
  })
}

export function useRetryImportMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => retryImport(id),
    onSuccess: (_data, id) => {
      // L'import se fait en tâche de fond côté Sonarr/Radarr : le statut
      // définitif viendra du prochain scan, mais la fiche est rechargée pour
      // refléter ce qui a déjà changé.
      queryClient.invalidateQueries({ queryKey: ["media", "detail", id] })
    },
  })
}

export function useHardlinkRepairPreviewMutation() {
  return useMutation({ mutationFn: (id: number) => hardlinkRepairPreview(id) })
}

/** Chargé seulement à l'ouverture du dialogue : la recherche interroge
 * Sonarr/Radarr, inutile de la lancer sur chaque fiche. */
export function useArrLinkPreviewQuery(id: number, enabled: boolean) {
  return useQuery({ queryKey: ["media", "arr-link", id], queryFn: () => getArrLinkPreview(id), enabled, retry: false })
}

export function useArrLinkMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: ArrLinkRequest }) => linkMediaToArr(id, payload),
    // Le média devient suivi : sa fiche, la bibliothèque et l'historique changent.
    onSuccess: (_result, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["media"] })
      queryClient.invalidateQueries({ queryKey: ["media", "detail", id] })
      queryClient.invalidateQueries({ queryKey: ["history"] })
    },
  })
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
