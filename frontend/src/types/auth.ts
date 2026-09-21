export interface AuthStatus {
  setup_required: boolean
  authenticated: boolean
}

export interface CurrentUser {
  username: string
  two_factor_enabled: boolean
}

export interface SetupRequest {
  username: string
  password: string
}

export interface LoginRequest {
  username: string
  password: string
  // Code de l'application d'authentification ou code de secours (2FA activée).
  otp?: string
}

export interface ChangePasswordRequest {
  current_password: string
  new_password: string
}

export interface ChangeUsernameRequest {
  username: string
  password: string
}

export interface TwoFactorSetup {
  secret: string
  otpauth_uri: string
}

export interface RecoveryCodes {
  codes: string[]
}

export interface TwoFactorDisableRequest {
  password: string
  code: string
}

/** Journal des connexions (Réglages → Compte). */
export interface LoginAttempt {
  created_at: string
  username: string
  ip: string
  success: boolean
  reason: string | null
}

export interface SecuritySettings {
  /** Reverse-proxys de confiance, IP ou CIDR séparés par des virgules. */
  trusted_proxies: string
}
