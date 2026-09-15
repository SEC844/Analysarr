export type ServiceKey = "emby" | "sonarr" | "radarr" | "qbittorrent" | "cross_seed" | "seer"

export interface ServiceStatus {
  service: ServiceKey
  // Nom affiché : « Emby »/« Jellyfin », nom de l'instance Sonarr/Radarr...
  name: string
  ok: boolean
  message: string
}

export interface ServicesStatus {
  checked_at: string
  services: ServiceStatus[]
}
