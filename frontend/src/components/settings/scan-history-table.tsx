import { CheckCircle2, Loader2, XCircle } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useScanHistoryQuery } from "@/hooks/use-scan"
import { useI18n } from "@/i18n"
import { formatDateTime } from "@/lib/format"

function formatDuration(startedAt: string, finishedAt: string | null): string {
  if (!finishedAt) return "—"
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime()
  if (!Number.isFinite(ms) || ms < 0) return "—"
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds} s`
  return `${Math.floor(seconds / 60)} min ${seconds % 60} s`
}

export function ScanHistoryTable() {
  const { t } = useI18n()
  const { data, isLoading } = useScanHistoryQuery()

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("scanHistory.title")}</CardTitle>
        <CardDescription>{t("scanHistory.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading && (
          <div className="text-muted-foreground flex items-center gap-2 py-4 text-sm">
            <Loader2 className="size-4 animate-spin" /> {t("common.loading")}
          </div>
        )}
        {data && data.length === 0 && <p className="text-muted-foreground py-4 text-sm">{t("scanHistory.empty")}</p>}
        {data && data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b text-left">
                  <th className="py-1.5 pr-4 font-medium">{t("scanHistory.columns.date")}</th>
                  <th className="py-1.5 pr-4 font-medium">{t("scanHistory.columns.trigger")}</th>
                  <th className="py-1.5 pr-4 font-medium">{t("scanHistory.columns.status")}</th>
                  <th className="py-1.5 pr-4 font-medium">{t("scanHistory.columns.duration")}</th>
                  <th className="py-1.5 pr-4 font-medium">{t("scanHistory.columns.media")}</th>
                  <th className="py-1.5 pr-4 font-medium">{t("scanHistory.columns.duplicates")}</th>
                  <th className="py-1.5 pr-4 font-medium">{t("scanHistory.columns.orphans")}</th>
                  <th className="py-1.5 font-medium">{t("scanHistory.columns.matched")}</th>
                </tr>
              </thead>
              <tbody>
                {data.map((run) => (
                  <tr key={run.id} className="border-border/60 border-b last:border-0">
                    <td className="py-1.5 pr-4 whitespace-nowrap">{formatDateTime(run.started_at) ?? run.started_at}</td>
                    <td className="py-1.5 pr-4">
                      <Badge variant="outline">
                        {run.trigger === "scheduled" ? t("scanHistory.scheduled") : t("scanHistory.manual")}
                      </Badge>
                    </td>
                    <td className="py-1.5 pr-4">
                      {run.status === "completed" && (
                        <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                          <CheckCircle2 className="size-4" /> {t("scanHistory.completed")}
                        </span>
                      )}
                      {run.status === "failed" && (
                        <span className="text-destructive inline-flex items-center gap-1" title={run.error_message ?? undefined}>
                          <XCircle className="size-4" /> {t("scanHistory.failed")}
                        </span>
                      )}
                      {run.status === "running" && (
                        <span className="inline-flex items-center gap-1">
                          <Loader2 className="size-4 animate-spin" /> {t("scanHistory.running")}
                        </span>
                      )}
                    </td>
                    <td className="py-1.5 pr-4 whitespace-nowrap">{formatDuration(run.started_at, run.finished_at)}</td>
                    <td className="py-1.5 pr-4">{run.media_count}</td>
                    <td className="py-1.5 pr-4">{run.duplicate_count}</td>
                    <td className="py-1.5 pr-4">{run.orphan_count}</td>
                    <td className="py-1.5">
                      {run.qbittorrent_matched_count} / {run.qbittorrent_torrent_count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
