import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { changePassword, getAuthStatus, getCurrentUser, login, logout, setupAdmin } from "@/lib/api"
import type { ChangePasswordRequest, LoginRequest, SetupRequest } from "@/types/auth"

export const AUTH_STATUS_QUERY_KEY = ["auth", "status"] as const
export const CURRENT_USER_QUERY_KEY = ["auth", "me"] as const

export function useAuthStatusQuery() {
  return useQuery({
    queryKey: AUTH_STATUS_QUERY_KEY,
    queryFn: getAuthStatus,
  })
}

export function useCurrentUserQuery(enabled: boolean) {
  return useQuery({
    queryKey: CURRENT_USER_QUERY_KEY,
    queryFn: getCurrentUser,
    enabled,
  })
}

export function useSetupAdminMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: SetupRequest) => setupAdmin(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AUTH_STATUS_QUERY_KEY })
    },
  })
}

export function useLoginMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: LoginRequest) => login(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AUTH_STATUS_QUERY_KEY })
    },
  })
}

export function useLogoutMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => logout(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AUTH_STATUS_QUERY_KEY })
      queryClient.removeQueries({ queryKey: CURRENT_USER_QUERY_KEY })
    },
  })
}

export function useChangePasswordMutation() {
  return useMutation({
    mutationFn: (payload: ChangePasswordRequest) => changePassword(payload),
  })
}
