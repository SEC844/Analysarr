import { useState } from "react"
import { CheckCircle2, ChevronRight, Loader2, Trash2, XCircle } from "lucide-react"
import { Link } from "react-router-dom"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useActionHistoryQuery, useClearActionHistoryMutation } from "@/hooks/use-history"
import { useI18n } from "@/i18n"
import { formatBytes, formatDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { ActionLogEntry } from "@/types/history"

function HistoryRow({ entry }: { entry: ActionLogEntry }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const hasDetails = entry.details.length > 0

  return (
    <li className="py-2">
      <div className="flex items-center gap-3 text-sm">
        <button
          type="button"
          disabled={!hasDetails}
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={t(open ? "history.hideDetails" : "history.showDetails", { name: entry.media_title })}
          className="text-muted-foreground hover:text-foreground shrink-0 disabled:invisible"
        >
          <ChevronRight className={cn("size-4 transition-transform", open && "rotate-90")} />
        </button>
        <span className="text-muted-foreground w-36 shrink-0 whitespace-nowrap">
          {formatDateTime(entry.created_at) ?? entry.created_at}
        </span>
        <Badge variant="outline" className="shrink-0">
          {t(`history.actions.${entry.action}`)}
        </Badge>
        <span className="min-w-0 flex-1 truncate" title={entry.media_title}>
          {entry.media_id !== null ? (
            <Link to={`/media/${entry.media_id}`} className="hover:underline">
              {entry.media_title}
            </Link>
          ) : (
            entry.media_title
          )}
        </span>
        <span className="flex shrink-0 items-center gap-3 whitespace-nowrap">
          {entry.freed_bytes ? (
            <span className="text-muted-foreground">{t("history.freed", { size: formatBytes(entry.freed_bytes) })}</span>
          ) : null}
          {entry.success_count > 0 && (
            <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="size-4" /> {t("history.succeeded", { count: entry.success_count })}
            </span>
          )}
          {entry.failure_count > 0 && (
            <span className="text-destructive inline-flex items-center gap-1">
              <XCircle className="size-4" /> {t("history.failures", { count: entry.failure_count })}
            </span>
          )}
        </span>
      </div>
      {open && (
        <ul className="border-border mt-2 ml-7 space-y-1 border-l pl-3 text-sm">
          {entry.details.map((step, i) => (
            <li key={i} className="flex min-w-0 items-start gap-2">
              {step.success ? (
                <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
              ) : (
                <XCircle className="text-destructive mt-0.5 size-3.5 shrink-0" />
              )}
              <span className="min-w-0 break-all">
                {step.label}
                {step.error && <span className="text-destructive"> — {step.error}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

export function ActionHistory() {
  const { t } = useI18n()
  const { data, isLoading, isError } = useActionHistoryQuery()
  const clear = useClearActionHistoryMutation()
  const [confirming, setConfirming] = useState(false)

  function handleClear() {
    if (!confirming) {
      setConfirming(true)
      return
    }
    clear.mutate(undefined, {
      onSuccess: () => toast.success(t("history.cleared")),
      onError: (err) => toast.error(err instanceof Error ? err.message : t("common.saveFailed")),
      onSettled: () => setConfirming(false),
    })
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>{t("history.title")}</CardTitle>
            <CardDescription>{t("history.description")}</CardDescription>
          </div>
          {data && data.length > 0 && (
            <Button
              type="button"
              size="sm"
              variant={confirming ? "destructive" : "ghost"}
              disabled={clear.isPending}
              onClick={handleClear}
              onBlur={() => setConfirming(false)}
            >
              {clear.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
              {confirming ? t("history.confirmClear") : t("history.clear")}
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading && (
          <div className="text-muted-foreground flex items-center gap-2 py-4 text-sm">
            <Loader2 className="size-4 animate-spin" /> {t("common.loading")}
          </div>
        )}
        {isError && <p className="text-destructive py-4 text-sm">{t("common.apiUnreachable")}</p>}
        {data && data.length === 0 && <p className="text-muted-foreground py-4 text-sm">{t("history.empty")}</p>}
        {data && data.length > 0 && (
          <ul className="divide-border divide-y">
            {data.map((entry) => (
              <HistoryRow key={entry.id} entry={entry} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
