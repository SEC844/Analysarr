import { Link } from "react-router-dom"
import { Loader2, PauseCircle, Play } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { useAutomationGuardQuery, useResumeAutomationsMutation } from "@/hooks/use-automations"
import { useI18n, type MessageKey } from "@/i18n"

const AUTOMATIONS_SETTINGS = "/settings?section=automations"

/** Automatisations suspendues par le garde-fou : l'utilisateur vérifie ses
 * montages et ses services, puis les relance lui-même. Affiché sur l'accueil
 * (lien vers les réglages) et dans les réglages (bouton de reprise). */
export function AutomationsPausedBanner({ withResume = false }: { withResume?: boolean }) {
  const { t } = useI18n()
  const { data: guard } = useAutomationGuardQuery()
  const resumeMutation = useResumeAutomationsMutation()

  if (!guard?.paused) return null

  const reason =
    guard.status && guard.previous !== null && guard.current !== null
      ? t("automations.guard.reason", {
          status: t(`status.${guard.status}` as MessageKey),
          previous: guard.previous,
          current: guard.current,
          percent: guard.changed_percent ?? 0,
        })
      : t("automations.guard.reasonUnknown")

  return (
    <div
      className="flex flex-wrap items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300"
      role="status"
    >
      <PauseCircle className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1 space-y-1">
        <p className="font-medium">{t("automations.guard.pausedTitle")}</p>
        <p className="text-amber-700/90 dark:text-amber-300/90">{reason}</p>
        <p className="text-amber-700/90 dark:text-amber-300/90">{t("automations.guard.pausedAdvice")}</p>
      </div>
      {withResume ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={resumeMutation.isPending}
          onClick={() =>
            resumeMutation.mutate(undefined, {
              onSuccess: () => toast.success(t("automations.guard.resumed")),
              onError: (err) => toast.error(err instanceof Error ? err.message : t("common.saveFailed")),
            })
          }
        >
          {resumeMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
          {t("automations.guard.resume")}
        </Button>
      ) : (
        <Button type="button" variant="outline" size="sm" render={<Link to={AUTOMATIONS_SETTINGS} />}>
          {t("automations.guard.open")}
        </Button>
      )}
    </div>
  )
}
