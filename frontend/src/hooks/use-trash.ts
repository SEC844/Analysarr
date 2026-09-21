import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  deleteTrashEntry,
  emptyTrash,
  getTrash,
  getTrashSettings,
  restoreTrashEntry,
  saveTrashSettings,
} from "@/lib/api"
import type { TrashSettings } from "@/types/trash"

const TRASH_QUERY_KEY = ["trash"] as const
const TRASH_SETTINGS_QUERY_KEY = ["trash", "settings"] as const

export function useTrashQuery() {
  return useQuery({ queryKey: TRASH_QUERY_KEY, queryFn: getTrash })
}

/** Réglages de la corbeille : lus aussi par le dialogue de suppression, pour
 * dire ce qu'il adviendra vraiment des fichiers cochés. */
export function useTrashSettingsQuery() {
  return useQuery({ queryKey: TRASH_SETTINGS_QUERY_KEY, queryFn: getTrashSettings })
}

export function useSaveTrashSettingsMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: { enabled: boolean; retention_days: number }) => saveTrashSettings(payload),
    onSuccess: (data: TrashSettings) => queryClient.setQueryData(TRASH_SETTINGS_QUERY_KEY, data),
  })
}

function useTrashInvalidation() {
  const queryClient = useQueryClient()
  return () => {
    queryClient.invalidateQueries({ queryKey: TRASH_QUERY_KEY })
    // Un fichier restauré revient dans la bibliothèque au prochain scan.
    queryClient.invalidateQueries({ queryKey: ["media"] })
  }
}

export function useRestoreTrashMutation() {
  const invalidate = useTrashInvalidation()
  return useMutation({ mutationFn: (id: number) => restoreTrashEntry(id), onSuccess: invalidate })
}

export function useDeleteTrashMutation() {
  const invalidate = useTrashInvalidation()
  return useMutation({ mutationFn: (id: number) => deleteTrashEntry(id), onSuccess: invalidate })
}

export function useEmptyTrashMutation() {
  const invalidate = useTrashInvalidation()
  return useMutation({ mutationFn: () => emptyTrash(), onSuccess: invalidate })
}
