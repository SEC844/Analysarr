import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  getCleanupCandidate,
  getCleanupCandidates,
  getCleanupSettings,
  getForecast,
  saveCleanupSettings,
  simulateCleanupPlan,
} from "@/lib/api"
import type { CleanupPlanRequest, CleanupQuery, CleanupSettings } from "@/types/cleanup"

const CLEANUP_QUERY_KEY = ["cleanup"] as const

export function useCleanupCandidatesQuery(query: CleanupQuery) {
  return useQuery({
    queryKey: [...CLEANUP_QUERY_KEY, "candidates", query],
    queryFn: () => getCleanupCandidates(query),
    // Pas de liste vide qui clignote en changeant de page ou de filtre.
    placeholderData: keepPreviousData,
  })
}

export function useCleanupCandidateQuery(id: number, enabled: boolean) {
  return useQuery({
    queryKey: [...CLEANUP_QUERY_KEY, "candidate", id],
    queryFn: () => getCleanupCandidate(id),
    enabled,
  })
}

export function useCleanupSettingsQuery() {
  return useQuery({ queryKey: [...CLEANUP_QUERY_KEY, "settings"], queryFn: getCleanupSettings })
}

export function useSaveCleanupSettingsMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (settings: CleanupSettings) => saveCleanupSettings(settings),
    // Réglages changés : tout le classement est à relire.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CLEANUP_QUERY_KEY }),
  })
}

/** Prévisions d'espace disque : une photographie par jour, rien ne change
 * d'une minute à l'autre. */
export function useForecastQuery() {
  return useQuery({ queryKey: ["library", "forecast"], queryFn: getForecast, staleTime: 10 * 60_000 })
}

/** Simulation du mode objectif : sans effet, donc rien à invalider. */
export function useCleanupPlanMutation() {
  return useMutation({ mutationFn: (payload: CleanupPlanRequest) => simulateCleanupPlan(payload) })
}
