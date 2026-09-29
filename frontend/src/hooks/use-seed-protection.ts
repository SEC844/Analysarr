import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { dismissSeedProtectionPrompt, getSeedProtection, saveSeedProtection } from "@/lib/api"
import type { SeedProtection, SeedProtectionWrite } from "@/types/seed"

const SEED_PROTECTION_QUERY_KEY = ["seed-protection"] as const

export function useSeedProtectionQuery() {
  return useQuery({ queryKey: SEED_PROTECTION_QUERY_KEY, queryFn: getSeedProtection })
}

function useStore() {
  const queryClient = useQueryClient()
  return (data: SeedProtection) => {
    queryClient.setQueryData(SEED_PROTECTION_QUERY_KEY, data)
    // Les obligations affichées sur les fiches dépendent de ces réglages.
    queryClient.invalidateQueries({ queryKey: ["media"] })
  }
}

export function useSaveSeedProtectionMutation() {
  const store = useStore()
  return useMutation({
    mutationFn: (payload: SeedProtectionWrite) => saveSeedProtection(payload),
    onSuccess: store,
  })
}

export function useDismissSeedPromptMutation() {
  const store = useStore()
  return useMutation({ mutationFn: () => dismissSeedProtectionPrompt(), onSuccess: store })
}
