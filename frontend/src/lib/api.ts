import type { DiagnosticsResult } from "@/types/diagnostics"
import type {
  CrossSeedSearchResult,
  DeleteExecuteResult,
  DeletePreview,
  HardlinkRepairPreview,
  HardlinkRepairResult,
  MediaDetail,
  MediaListParams,
  MediaListResponse,
  ScanRunRead,
} from "@/types/media"
import type {
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
    const body = await res.text()
    throw new Error(body || `Erreur HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
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

export function posterUrl(id: number): string {
  return `/api/media/${id}/poster`
}

export function deletePreview(id: number): Promise<DeletePreview> {
  return request<DeletePreview>(`/api/media/${id}/delete/preview`, { method: "POST" })
}

export function deleteExecute(id: number): Promise<DeleteExecuteResult> {
  return request<DeleteExecuteResult>(`/api/media/${id}/delete/execute`, { method: "POST" })
}

export function crossSeedSearch(id: number): Promise<CrossSeedSearchResult> {
  return request<CrossSeedSearchResult>(`/api/media/${id}/cross-seed-search`, { method: "POST" })
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
