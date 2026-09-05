export interface AuthStatus {
  setup_required: boolean
  authenticated: boolean
}

export interface CurrentUser {
  username: string
}

export interface SetupRequest {
  username: string
  password: string
}

export interface LoginRequest {
  username: string
  password: string
}

export interface ChangePasswordRequest {
  current_password: string
  new_password: string
}
