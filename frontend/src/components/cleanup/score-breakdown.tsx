import { useState } from "react"
import { Loader2 } from "lucide-react"

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { useCleanupCandidateQuery } from "@/hooks/use-cleanup"
import { useI18n } from "@/i18n"
import { componentDetail, componentName, scoreTone } from "@/lib/cleanup"
import { cn } from "@/lib/utils"
import type { CleanupCandidate } from "@/types/cleanup"

const TONES = {
  high: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  medium: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  low: "bg-muted text-muted-foreground",
} as const

/** Badge de score ; au clic, la décomposition complète (chargée à la
 * demande : le détail n'est calculé que pour ce média). */
export function ScoreBreakdown({ candidate }: { candidate: CleanupCandidate }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const { data: detail, isLoading, isError } = useCleanupCandidateQuery(candidate.media_id, open)

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            title={t("cleanup.scoreHint")}
            className={cn(
              "inline-flex h-7 min-w-10 items-center justify-center rounded-md px-2 text-sm font-semibold tabular-nums",
              TONES[scoreTone(candidate.score)],
            )}
          />
        }
      >
        {candidate.score}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <p className="px-1 pb-2 text-sm font-medium">{t("cleanup.breakdown.title")}</p>
        {isLoading && (
          <p className="text-muted-foreground flex items-center gap-2 px-1 text-xs">
            <Loader2 className="size-3.5 animate-spin" /> {t("common.loading")}
          </p>
        )}
        {isError && <p className="text-destructive px-1 text-xs">{t("cleanup.breakdown.failed")}</p>}
        {detail && (
          <div className="space-y-2">
            <ul className="divide-border divide-y border-t">
              {detail.components.map((component) => (
                <li key={component.key} className="space-y-1 px-1 py-2">
                  <div className="flex items-baseline justify-between gap-2 text-sm">
                    <span className="font-medium">{t(componentName(component.key))}</span>
                    <span className="tabular-nums">{component.value}</span>
                  </div>
                  <div className="bg-muted h-1 overflow-hidden rounded-full">
                    <div className="bg-primary h-full rounded-full" style={{ width: `${component.value}%` }} />
                  </div>
                  <p className="text-muted-foreground flex justify-between gap-2 text-[11px]">
                    <span>{componentDetail(t, component)}</span>
                    <span className="shrink-0">{t("cleanup.breakdown.weight", { weight: component.weight })}</span>
                  </p>
                </li>
              ))}
            </ul>
            {detail.in_progress_users.length > 0 && (
              <p className="px-1 text-xs text-amber-700 dark:text-amber-300">
                {t("cleanup.breakdown.malus", {
                  names: detail.in_progress_users.join(", "),
                  raw: detail.raw_score,
                  score: detail.score,
                })}
              </p>
            )}
            <p className="border-t px-1 pt-2 text-sm font-medium">
              {t("cleanup.breakdown.total", { score: detail.score })}
            </p>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}
