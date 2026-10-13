import { useState } from "react"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { useCleanupSettingsQuery, useSaveCleanupSettingsMutation } from "@/hooks/use-cleanup"
import { useI18n } from "@/i18n"
import { allocateWeight, componentName, WEIGHT_BUDGET, WEIGHT_KEYS, weightsTotal } from "@/lib/cleanup"
import { cn } from "@/lib/utils"
import type { CleanupPreset, CleanupSettings, CleanupSettingsRead, CleanupWeights } from "@/types/cleanup"

const PRESETS: CleanupPreset[] = ["prudent", "balanced", "space_first"]

// Bornes identiques à schemas/cleanup.py (le serveur revérifie tout).
const DAY_FIELDS = [
  { key: "disinterest_days", label: "cleanup.settings.disinterestDays", min: 30, max: 1825 },
  { key: "age_days", label: "cleanup.settings.ageDays", min: 30, max: 3650 },
  { key: "inactive_days", label: "cleanup.settings.inactiveDays", min: 7, max: 730 },
  { key: "recent_days", label: "cleanup.settings.recentDays", min: 0, max: 365 },
] as const

function daysInBounds(settings: CleanupSettings): boolean {
  return DAY_FIELDS.every(({ key, min, max }) => {
    const value = settings[key]
    return Number.isInteger(value) && value >= min && value <= max
  })
}

/** Les 100 points de la note, répartis entre les critères : un curseur
 * s'arrête quand il n'en reste plus, sans jamais faire reculer les autres. */
function WeightBudget({
  weights,
  onChange,
}: {
  weights: CleanupWeights
  onChange: (weights: CleanupWeights) => void
}) {
  const { t } = useI18n()
  const used = weightsTotal(weights)
  const left = WEIGHT_BUDGET - used

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <p className="text-sm font-medium">{t("cleanup.settings.weights")}</p>
          <p
            className={cn(
              "text-xs tabular-nums",
              left > 0 ? "text-amber-700 dark:text-amber-300" : "text-muted-foreground",
            )}
            aria-live="polite"
          >
            {left > 0 ? t("cleanup.settings.budgetLeft", { count: left }) : t("cleanup.settings.budgetFull")}
          </p>
        </div>
        <div
          className="bg-muted h-1.5 overflow-hidden rounded-full"
          role="meter"
          aria-label={t("cleanup.settings.weights")}
          aria-valuemin={0}
          aria-valuemax={WEIGHT_BUDGET}
          aria-valuenow={used}
        >
          <div className="bg-primary h-full rounded-full transition-[width]" style={{ width: `${used}%` }} />
        </div>
        <p className="text-muted-foreground text-xs">{t("cleanup.settings.budgetHint")}</p>
      </div>
      <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {WEIGHT_KEYS.map((key) => (
          <div key={key} className="space-y-1">
            <div className="flex items-baseline justify-between gap-2">
              <Label htmlFor={`cleanup-weight-${key}`} className="text-xs">
                {t(componentName(key))}
              </Label>
              <span className="text-muted-foreground text-xs tabular-nums">
                {t("cleanup.settings.points", { count: weights[key] })}
              </span>
            </div>
            <input
              id={`cleanup-weight-${key}`}
              type="range"
              min={0}
              max={WEIGHT_BUDGET}
              step={1}
              value={weights[key]}
              onChange={(e) => onChange(allocateWeight(weights, key, Number(e.target.value)))}
              className="accent-primary w-full"
            />
          </div>
        ))}
      </div>
    </div>
  )
}

function Form({ data }: { data: CleanupSettingsRead }) {
  const { t } = useI18n()
  const save = useSaveCleanupSettingsMutation()
  const [draft, setDraft] = useState<CleanupSettings>(data.settings)
  const budgetFull = weightsTotal(draft.weights) === WEIGHT_BUDGET
  const daysValid = daysInBounds(draft)

  // Toute retouche passe en mode personnalisé.
  const change = (changes: Partial<CleanupSettings>) => setDraft((current) => ({ ...current, ...changes, preset: null }))

  return (
    <div className="space-y-5">
      <p className="text-muted-foreground text-xs">{t("cleanup.settings.description")}</p>
      <div className="space-y-2">
        <div className="flex flex-wrap gap-2" role="group" aria-label={t("cleanup.settings.mode")}>
          {PRESETS.map((preset) => (
            <Button
              key={preset}
              type="button"
              size="sm"
              variant={draft.preset === preset ? "default" : "outline"}
              aria-pressed={draft.preset === preset}
              onClick={() => setDraft(data.presets[preset])}
            >
              {t(`cleanup.settings.presets.${preset}`)}
            </Button>
          ))}
          <Button
            type="button"
            size="sm"
            variant={draft.preset === null ? "default" : "outline"}
            aria-pressed={draft.preset === null}
            onClick={() => setDraft((current) => ({ ...current, preset: null }))}
          >
            {t("cleanup.settings.custom")}
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">{t(`cleanup.settings.modeHints.${draft.preset ?? "custom"}`)}</p>
      </div>

      <WeightBudget weights={draft.weights} onChange={(weights) => change({ weights })} />

      <div className="space-y-1.5">
        <Label htmlFor="cleanup-space-priority">
          {t("cleanup.settings.spacePriority", { value: draft.space_priority })}
        </Label>
        <input
          id="cleanup-space-priority"
          type="range"
          min={0}
          max={100}
          step={5}
          value={draft.space_priority}
          onChange={(e) => change({ space_priority: Number(e.target.value) })}
          className="accent-primary w-full"
        />
        <p className="text-muted-foreground text-xs">{t("cleanup.settings.spacePriorityHint")}</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {DAY_FIELDS.map(({ key, label, min, max }) => (
          <div key={key} className="space-y-1.5">
            <Label htmlFor={`cleanup-${key}`}>{t(label)}</Label>
            <div className="flex items-center gap-2">
              <Input
                id={`cleanup-${key}`}
                type="number"
                min={min}
                max={max}
                className="w-28"
                value={Number.isNaN(draft[key]) ? "" : draft[key]}
                onChange={(e) => change({ [key]: e.target.value === "" ? Number.NaN : Number(e.target.value) })}
              />
              <span className="text-muted-foreground text-sm">{t("cleanup.settings.days")}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          disabled={!budgetFull || !daysValid || save.isPending}
          onClick={() =>
            save.mutate(draft, {
              onSuccess: () => toast.success(t("cleanup.settings.saved")),
              onError: (err) => toast.error(err instanceof Error ? err.message : t("common.saveFailed")),
            })
          }
        >
          {save.isPending && <Loader2 className="size-4 animate-spin" />}
          {t("cleanup.settings.save")}
        </Button>
        {!daysValid && (
          <p className="text-destructive text-xs" role="alert">
            {t("cleanup.settings.invalid")}
          </p>
        )}
      </div>
    </div>
  )
}

/** Réglages du classement, ouverts depuis la barre d'outils de l'assistant. */
export function CleanupSettingsPanel({ id }: { id: string }) {
  const { data } = useCleanupSettingsQuery()

  return (
    <div id={id} className="border-border rounded-lg border p-4">
      {/* Remonté après enregistrement : le brouillon repart des valeurs du serveur. */}
      {data ? <Form key={JSON.stringify(data.settings)} data={data} /> : <Skeleton className="h-40 w-full" />}
    </div>
  )
}
