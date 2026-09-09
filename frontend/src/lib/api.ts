import type {
  AuthStatus,
  ChangePasswordRequest,
  CurrentUser,
  LoginRequest,
  SetupRequest,
} from "@/types/auth"
import type { DiagnosticsResult } from "@/types/diagnostics"
import type {
  CrossSeedSearchResult,
  DeleteExecuteResult,
  DeletePreview,
  HardlinkRepairPreview,
  HardlinkRepairResult,
  MediaDeleteSelection,
  MediaDeleteSelectionResult,
  MediaDetail,
  MediaListParams,
  MediaListResponse,
  ScanRunRead,
} from "@/types/media"
import type {
  BrowseResult,
  ConnectionTestRequest,
  ConnectionTestResult,
  ServiceName,
  SettingsRead,
  SettingsWrite,
} from "@/types/settings"

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  })
  if (!res.ok) {
    // Session expirée/absente en cours d'usage (pas sur les appels d'auth
    // eux-mêmes, où un 401 est une réponse normale à afficher dans le
    // formulaire) : on revient à l'accueil, qui réévaluera /api/auth/status
    // et affichera l'écran de connexion.
    if (res.status === 401 && !path.startsWith("/api/auth/")) {
      window.location.assign("/")
    }
    const body = await res.text()
    // FastAPI renvoie {"detail": "..."} — sans ça, l'erreur affichée à
    // l'utilisateur est le JSON brut plutôt que le message lisible.
    let message = body
    try {
      const parsed = JSON.parse(body)
      if (typeof parsed?.detail === "string") message = parsed.detail
    } catch {
      // corps non-JSON : on garde le texte brut
    }
    throw new Error(message || `Erreur HTTP ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export function getAuthStatus(): Promise<AuthStatus> {
  return request<AuthStatus>("/api/auth/status")
}

export function setupAdmin(payload: SetupRequest): Promise<CurrentUser> {
  return request<CurrentUser>("/api/auth/setup", { method: "POST", body: JSON.stringify(payload) })
}

export function login(payload: LoginRequest): Promise<CurrentUser> {
  return request<CurrentUser>("/api/auth/login", { method: "POST", body: JSON.stringify(payload) })
}

export function logout(): Promise<void> {
  return request<void>("/api/auth/logout", { method: "POST" })
}

export function getCurrentUser(): Promise<CurrentUser> {
  return request<CurrentUser>("/api/auth/me")
}

export function changePassword(payload: ChangePasswordRequest): Promise<CurrentUser> {
  return request<CurrentUser>("/api/auth/password", { method: "PUT", body: JSON.stringify(payload) })
}

export function getSettings(): Promise<SettingsRead> {
  return request<SettingsRead>("/api/settings")
}

export function saveSettings(payload: SettingsWrite): Promise<SettingsRead> {
  return request<SettingsRead>("/api/settings", {
    method: "PUT",
    body: JSON.stringify(payload),
  })
}

export function browseFilesystem(path: string): Promise<BrowseResult> {
  return request<BrowseResult>(`/api/settings/browse?path=${encodeURIComponent(path)}`)
}

export function testConnection(
  service: ServiceName,
  payload: ConnectionTestRequest,
): Promise<ConnectionTestResult> {
  return request<ConnectionTestResult>(`/api/settings/test/${service}`, {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export function listMedia(params: MediaListParams): Promise<MediaListResponse> {
  const search = new URLSearchParams()
  if (params.status) search.set("status", params.status)
  if (params.media_type) search.set("media_type", params.media_type)
  if (params.search) search.set("search", params.search)
  if (params.sort) search.set("sort", params.sort)
  const qs = search.toString()
  return request<MediaListResponse>(`/api/media${qs ? `?${qs}` : ""}`)
}

export function getMedia(id: number): Promise<MediaDetail> {
  return request<MediaDetail>(`/api/media/${id}`)
}

export function posterUrl(id: number, imageTag: string | null): string {
  // `v` rend l'URL propre à cette version précise de la jaquette : le
  // navigateur peut la mettre en cache indéfiniment (voir Cache-Control côté
  // backend) sans jamais risquer de servir une jaquette périmée — un
  // changement de jaquette change le tag, donc l'URL, donc force un nouveau
  // téléchargement automatiquement.
  return imageTag ? `/api/media/${id}/poster?v=${encodeURIComponent(imageTag)}` : `/api/media/${id}/poster`
}

export function deletePreview(id: number): Promise<DeletePreview> {
  return request<DeletePreview>(`/api/media/${id}/delete/preview`, { method: "POST" })
}

export function deleteExecute(id: number): Promise<DeleteExecuteResult> {
  return request<DeleteExecuteResult>(`/api/media/${id}/delete/execute`, { method: "POST" })
}

export function deleteSelectionExecute(id: number, selection: MediaDeleteSelection): Promise<MediaDeleteSelectionResult> {
  return request<MediaDeleteSelectionResult>(`/api/media/${id}/delete-selection`, {
    method: "POST",
    body: JSON.stringify(selection),
  })
}

export type CrossSeedSearchScope = "episode" | "season" | "series"

export function crossSeedSearch(id: number, scope: CrossSeedSearchScope = "episode"): Promise<CrossSeedSearchResult> {
  return request<CrossSeedSearchResult>(`/api/media/${id}/cross-seed-search?scope=${scope}`, { method: "POST" })
}

export function hardlinkRepairPreview(id: number): Promise<HardlinkRepairPreview> {
  return request<HardlinkRepairPreview>(`/api/media/${id}/hardlink-repair/preview`, { method: "POST" })
}

export function hardlinkRepairExecute(id: number): Promise<HardlinkRepairResult> {
  return request<HardlinkRepairResult>(`/api/media/${id}/hardlink-repair/execute`, { method: "POST" })
}

export function startScan(): Promise<{ started: boolean; message?: string }> {
  return request(`/api/scan`, { method: "POST" })
}

export function getScanStatus(): Promise<ScanRunRead | null> {
  return request<ScanRunRead | null>(`/api/scan/status`)
}

export function getPathDiagnostics(): Promise<DiagnosticsResult> {
  return request<DiagnosticsResult>(`/api/scan/diagnostics`)
}

export function getScanHistory(limit = 50): Promise<ScanRunRead[]> {
  return request<ScanRunRead[]>(`/api/scan/history?limit=${limit}`)
}
