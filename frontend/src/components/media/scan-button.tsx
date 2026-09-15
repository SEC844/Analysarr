import { Loader2, RefreshCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useScanRunner } from "@/hooks/use-scan"
import { useI18n, type MessageKey } from "@/i18n"

// Étapes envoyées par le backend (événements SSE) : identifiants fixes.
const STAGES = new Set(["radarr", "sonarr", "emby", "historique", "qbittorrent", "enregistrement", "visionnage"])

export function ScanButton() {
  const { t } = useI18n()
  const { start, isRunning, event } = useScanRunner()

  const stageLabel =
    event?.type === "progress" && event.stage
      ? STAGES.has(event.stage)
        ? t(`scan.stages.${event.stage}` as MessageKey)
        : event.stage
      : t("scan.starting")

  return (
    <div className="flex items-center gap-3">
      {isRunning && <span className="text-muted-foreground text-sm">{stageLabel}</span>}
      {!isRunning && event?.type === "failed" && (
        <span className="text-destructive text-sm">{t("scan.failed", { message: event.message ?? "" })}</span>
      )}
      {!isRunning && event?.type === "completed" && (
        <span className="text-muted-foreground text-sm">
          {t("scan.summary", {
            media: event.media_count ?? 0,
            duplicates: event.duplicate_count ?? 0,
            orphans: event.orphan_count ?? 0,
          })}
          {event.qbittorrent_torrent_count !== undefined && event.qbittorrent_matched_count !== undefined && (
            <>
              {" · "}
              <span
                title={t("scan.matchedHint")}
                className={
                  event.qbittorrent_torrent_count - event.qbittorrent_matched_count > 0
                    ? "text-amber-500 dark:text-amber-400"
                    : undefined
                }
              >
                {t("scan.matched", { matched: event.qbittorrent_matched_count, total: event.qbittorrent_torrent_count })}
              </span>
            </>
          )}
        </span>
      )}
      <Button type="button" onClick={start} disabled={isRunning}>
        {isRunning ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
        {t("scan.button")}
      </Button>
    </div>
  )
}
