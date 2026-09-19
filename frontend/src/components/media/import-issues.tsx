import { useState } from "react"
import { CheckCircle2, Loader2, PackageX, RefreshCw, XCircle } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { useRetryImportMutation } from "@/hooks/use-media"
import { useI18n } from "@/i18n"
import { formatBytes } from "@/lib/format"
import type { DeleteStepResult, ImportIssueRead } from "@/types/media"

/** Téléchargements que Sonarr/Radarr n'a pas réussi à ranger dans la
 * bibliothèque, avec le motif brut renvoyé par le serveur et un bouton pour
 * relancer l'import. Rien n'est supprimé ici : la relance ne fait que
 * redemander l'import du fichier déjà téléchargé. */
export function ImportIssues({ mediaId, issues }: { mediaId: number; issues: ImportIssueRead[] }) {
  const { t } = useI18n()
  const retry = useRetryImportMutation()
  const [steps, setSteps] = useState<DeleteStepResult[] | null>(null)

  if (issues.length === 0) return null

  const canRetry = issues.some((issue) => issue.can_retry)

  function handleRetry() {
    retry.mutate(mediaId, {
      onSuccess: (result) => {
        setSteps(result.steps)
        if (result.imported_files > 0) toast.success(t("importIssues.retried", { count: result.imported_files }))
        else toast.error(t("importIssues.nothingImported"))
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : t("common.saveFailed")),
    })
  }

  return (
    <div className="border-border space-y-3 rounded-lg border p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-medium">
            <PackageX className="size-4 shrink-0 text-amber-500" />
            {t("importIssues.title", { count: issues.length })}
          </p>
          <p className="text-muted-foreground text-sm">{t("importIssues.description")}</p>
        </div>
        <Button type="button" variant="secondary" size="sm" disabled={!canRetry || retry.isPending} onClick={handleRetry}>
          {retry.isPending ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          {t("importIssues.retry")}
        </Button>
      </div>

      <ul className="space-y-2 text-sm">
        {issues.map((issue) => (
          <li key={issue.id} className="min-w-0">
            <p className="flex items-center gap-2">
              <span className="truncate" title={issue.title}>
                {issue.episode_label ? `${issue.episode_label} · ` : ""}
                {issue.title}
              </span>
              {issue.size !== null && (
                <span className="text-muted-foreground shrink-0 text-xs">{formatBytes(issue.size)}</span>
              )}
            </p>
            {issue.reason && <p className="text-muted-foreground text-xs">{issue.reason}</p>}
            {!issue.can_retry && <p className="text-muted-foreground text-xs">{t("importIssues.noDownload")}</p>}
          </li>
        ))}
      </ul>

      {steps && (
        <ul className="space-y-1 text-sm">
          {steps.map((step, i) => (
            <li key={i} className="flex items-start gap-2">
              {step.success ? (
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-500" />
              ) : (
                <XCircle className="text-destructive mt-0.5 size-4 shrink-0" />
              )}
              <div className="min-w-0">
                <p className="break-all">{step.label}</p>
                {step.error && <p className="text-destructive text-xs">{step.error}</p>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
