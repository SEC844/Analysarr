import { useMutation } from "@tanstack/react-query"
import { AlertTriangle, CheckCircle2, Loader2, Search, XCircle } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useI18n } from "@/i18n"
import { getPathDiagnostics } from "@/lib/api"
import type { PathDiagnostics } from "@/types/diagnostics"

function DiagnosticsBlock({ title, diag }: { title: string; diag: PathDiagnostics }) {
  const { t, rich } = useI18n()
  const allResolved = diag.total > 0 && diag.resolved === diag.total
  const accessible = t("diagnostics.accessible", { title, resolved: diag.resolved, total: diag.total })
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        {diag.total === 0 ? (
          <span className="text-muted-foreground">{t("diagnostics.noItems", { title })}</span>
        ) : allResolved ? (
          <>
            <CheckCircle2 className="size-4 text-emerald-500" />
            {accessible}
          </>
        ) : (
          <>
            <XCircle className="text-destructive size-4" />
            {accessible}
          </>
        )}
      </div>
      {diag.common_unresolved_prefix && (
        <p className="border-destructive/30 bg-destructive/10 text-destructive flex items-start gap-2 rounded-md border p-2 text-xs">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>
            {rich("diagnostics.commonPrefix", {
              path: <code className="break-all">{diag.common_unresolved_prefix}</code>,
            })}
          </span>
        </p>
      )}
      {diag.unresolved_samples.length > 0 && (
        <ul className="border-border max-h-48 space-y-1 overflow-y-auto rounded-md border p-2 text-xs">
          {diag.unresolved_samples.map((c, i) => (
            <li key={i} className="text-muted-foreground">
              <span className="text-foreground">{c.label}</span>
              <br />
              <span className="break-all">{c.path ?? t("diagnostics.emptyPath")}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function PathDiagnosticsPanel() {
  const { t } = useI18n()
  const diagnostics = useMutation({ mutationFn: getPathDiagnostics })

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("diagnostics.title")}</CardTitle>
        <CardDescription>{t("diagnostics.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Button type="button" variant="secondary" disabled={diagnostics.isPending} onClick={() => diagnostics.mutate()}>
          {diagnostics.isPending ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
          {t("diagnostics.run")}
        </Button>

        {diagnostics.isError && (
          <p className="text-destructive text-sm">
            {diagnostics.error instanceof Error ? diagnostics.error.message : t("diagnostics.failed")}
          </p>
        )}

        {diagnostics.data && (
          <div className="space-y-4">
            <DiagnosticsBlock title="qBittorrent" diag={diagnostics.data.qbittorrent} />
            <DiagnosticsBlock title={t("diagnostics.embyMovies")} diag={diagnostics.data.emby} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
