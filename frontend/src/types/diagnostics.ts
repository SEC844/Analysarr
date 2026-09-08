export interface PathCheck {
  label: string
  path: string | null
  resolved: boolean
}

export interface PathDiagnostics {
  total: number
  resolved: number
  unresolved_samples: PathCheck[]
  common_unresolved_prefix: string | null
}

export interface DiagnosticsResult {
  qbittorrent: PathDiagnostics
  emby: PathDiagnostics
}
