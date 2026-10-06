import type { RealtimeStatus, SourceStatus } from "@/types/realtime"

/** Ce que montre l'en-tête : rien tant qu'aucun service n'est suivi ; une
 * pastille rouge dès qu'une source est en erreur. */
export function realtimeSummary(status: RealtimeStatus | undefined): "off" | "ok" | "error" {
  if (!status?.active) return "off"
  return status.sources.some((s) => s.state === "error") ? "error" : "ok"
}

/** Section des Réglages où se règle une source : celle du service concerné
 * (le temps réel n'a pas de page à lui). */
export function sourceSection(source: SourceStatus): string {
  switch (source.kind) {
    case "torrents":
      return "qbittorrent"
    case "media_server":
      return "emby"
    case "requests":
      return "seer"
    case "webhook":
      return source.key.split(":")[1] === "radarr" ? "radarr" : "sonarr"
  }
}

/** Première source en erreur, pour mener droit à son service. */
export function failingSource(status: RealtimeStatus | undefined): SourceStatus | undefined {
  return status?.sources.find((s) => s.state === "error")
}

/** Adresse d'Analysarr proposée à partir de celle du navigateur (souvent la
 * bonne quand tout tourne sur la même machine). */
export function detectedAddress(location: Pick<Location, "origin">): string {
  return location.origin
}
