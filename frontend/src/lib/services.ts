import type { ServiceKey, ServicesStatus, ServiceStatus } from "@/types/services"

// Section des Réglages de chaque service.
export const SERVICE_SECTIONS: Record<ServiceKey, string> = {
  emby: "emby",
  sonarr: "sonarr",
  radarr: "radarr",
  qbittorrent: "qbittorrent",
  cross_seed: "cross-seed",
  seer: "seer",
}

export interface SectionServiceStatus {
  ok: boolean
  down: ServiceStatus[]
}

/**
 * Statut par section des Réglages (une section Sonarr/Radarr regroupe toutes
 * ses instances) et première section dont un service ne répond pas.
 */
export function summarizeServices(status: ServicesStatus | undefined): {
  bySection: Record<string, SectionServiceStatus>
  firstDownSection: string | null
} {
  const bySection: Record<string, SectionServiceStatus> = {}
  let firstDownSection: string | null = null
  for (const service of status?.services ?? []) {
    const section = SERVICE_SECTIONS[service.service] ?? service.service
    const entry = (bySection[section] ??= { ok: true, down: [] })
    if (!service.ok) {
      entry.ok = false
      entry.down.push(service)
      firstDownSection ??= section
    }
  }
  return { bySection, firstDownSection }
}
