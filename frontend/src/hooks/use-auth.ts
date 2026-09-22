import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  changePassword,
  changeUsername,
  disableTwoFactor,
  enableTwoFactor,
  getAuthStatus,
  getCurrentUser,
  getLoginHistory,
  getSecuritySettings,
  login,
  logout,
  saveSecuritySettings,
  setupAdmin,
  setupTwoFactor,
} from "@/lib/api"
import type {
  ChangePasswordRequest,
  ChangeUsernameRequest,
  CurrentUser,
  LoginRequest,
  SetupRequest,
  TwoFactorDisableRequest,
} from "@/types/auth"

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

/** Journal des connexions : rafraîchi à l'ouverture de la section. */
export function useLoginHistoryQuery() {
  return useQuery({ queryKey: ["auth", "login-history"], queryFn: getLoginHistory })
}

export function useSecuritySettingsQuery() {
  return useQuery({ queryKey: ["auth", "security"], queryFn: getSecuritySettings })
}

export function useSaveSecuritySettingsMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (trustedProxies: string) => saveSecuritySettings(trustedProxies),
    onSuccess: (data) => queryClient.setQueryData(["auth", "security"], data),
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

function useCurrentUserUpdate() {
  const queryClient = useQueryClient()
  return (user: CurrentUser) => queryClient.setQueryData(CURRENT_USER_QUERY_KEY, user)
}

export function useChangeUsernameMutation() {
  const updateUser = useCurrentUserUpdate()
  return useMutation({
    mutationFn: (payload: ChangeUsernameRequest) => changeUsername(payload),
    onSuccess: updateUser,
  })
}

export function useTwoFactorSetupMutation() {
  return useMutation({ mutationFn: (password: string) => setupTwoFactor(password) })
}

export function useTwoFactorEnableMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (code: string) => enableTwoFactor(code),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CURRENT_USER_QUERY_KEY }),
  })
}

export function useTwoFactorDisableMutation() {
  const updateUser = useCurrentUserUpdate()
  return useMutation({
    mutationFn: (payload: TwoFactorDisableRequest) => disableTwoFactor(payload),
    onSuccess: updateUser,
  })
}
