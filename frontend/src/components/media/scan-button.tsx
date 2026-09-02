import { Loader2, RefreshCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useScanRunner } from "@/hooks/use-scan"

const STAGE_LABELS: Record<string, string> = {
  radarr: "Lecture de Radarr...",
  sonarr: "Lecture de Sonarr...",
  emby: "Lecture d'Emby...",
  historique: "Association des torrents...",
  qbittorrent: "Lecture de qBittorrent...",
  enregistrement: "Enregistrement des résultats...",
}

export function ScanButton() {
  const { start, isRunning, event } = useScanRunner()

  const stageLabel =
    event?.type === "progress" && event.stage ? STAGE_LABELS[event.stage] ?? event.stage : "Démarrage du scan..."

  return (
    <div className="flex items-center gap-3">
      {isRunning && <span className="text-muted-foreground text-sm">{stageLabel}</span>}
      {!isRunning && event?.type === "failed" && (
        <span className="text-destructive text-sm">Échec : {event.message}</span>
      )}
      {!isRunning && event?.type === "completed" && (
        <span className="text-muted-foreground text-sm">
          {event.media_count} médias · {event.duplicate_count} doublons · {event.orphan_count} orphelins
          {event.qbittorrent_torrent_count !== undefined && event.qbittorrent_matched_count !== undefined && (
            <>
              {" · "}
              <span
                title="Torrents qBittorrent rattachés à un média connu, sur le total présent dans qBittorrent. Un écart peut signaler un problème de correspondance (chemins, historique Sonarr/Radarr...)."
                className={
                  event.qbittorrent_torrent_count - event.qbittorrent_matched_count > 0
                    ? "text-amber-500 dark:text-amber-400"
                    : undefined
                }
              >
                {event.qbittorrent_matched_count}/{event.qbittorrent_torrent_count} torrents rattachés
              </span>
            </>
          )}
        </span>
      )}
      <Button type="button" onClick={start} disabled={isRunning}>
        {isRunning ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
        Scanner
      </Button>
    </div>
  )
}
