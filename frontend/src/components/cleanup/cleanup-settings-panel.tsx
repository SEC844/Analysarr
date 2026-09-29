import { useState } from "react"
import { ChevronRight, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useCleanupSettingsQuery, useSaveCleanupSettingsMutation } from "@/hooks/use-cleanup"
import { useI18n } from "@/i18n"
import { componentName } from "@/lib/cleanup"
import { cn } from "@/lib/utils"
import type { CleanupPreset, CleanupSettings, CleanupSettingsRead, CleanupWeights } from "@/types/cleanup"

const PRESETS: CleanupPreset[] = ["prudent", "balanced", "space_first"]
const WEIGHTS: (keyof CleanupWeights)[] = ["disinterest", "potential", "age", "series"]

// Bornes identiques à schemas/cleanup.py (le serveur revérifie tout).
const DAY_FIELDS = [
  { key: "disinterest_days", label: "cleanup.settings.disinterestDays", min: 30, max: 1825 },
  { key: "age_days", label: "cleanup.settings.ageDays", min: 30, max: 3650 },
  { key: "inactive_days", label: "cleanup.settings.inactiveDays", min: 7, max: 730 },
  { key: "recent_days", label: "cleanup.settings.recentDays", min: 0, max: 365 },
] as const

function isValidCleanupSettings(settings: CleanupSettings): boolean {
  const days = DAY_FIELDS.every(({ key, min, max }) => {
    const value = settings[key]
    return Number.isInteger(value) && value >= min && value <= max
  })
  const weights = WEIGHTS.map((key) => settings.weights[key])
  return (
    days &&
    settings.space_priority >= 0 &&
    settings.space_priority <= 100 &&
    weights.every((w) => w >= 0 && w <= 100) &&
    weights.some((w) => w > 0)
  )
}

function Form({ data }: { data: CleanupSettingsRead }) {
  const { t } = useI18n()
  const save = useSaveCleanupSettingsMutation()
  const [draft, setDraft] = useState<CleanupSettings>(data.settings)
  const valid = isValidCleanupSettings(draft)

  // Toute retouche fait quitter le préréglage.
  const change = (changes: Partial<CleanupSettings>) => setDraft((current) => ({ ...current, ...changes, preset: null }))

  return (
    <div className="space-y-5 pt-3">
      <p className="text-muted-foreground text-xs">{t("cleanup.settings.description")}</p>
      <div className="flex flex-wrap gap-2" role="group" aria-label={t("cleanup.settings.title")}>
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
        {draft.preset === null && (
          <span className="text-muted-foreground self-center text-xs">{t("cleanup.settings.custom")}</span>
        )}
      </div>

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

      <div className="space-y-2">
        <p className="text-sm font-medium">{t("cleanup.settings.weights")}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          {WEIGHTS.map((key) => (
            <div key={key} className="space-y-1">
              <Label htmlFor={`cleanup-weight-${key}`} className="text-xs">
                {t("cleanup.settings.weightValue", { name: t(componentName(key)), value: draft.weights[key] })}
              </Label>
              <input
                id={`cleanup-weight-${key}`}
                type="range"
                min={0}
                max={100}
                step={5}
                value={draft.weights[key]}
                onChange={(e) => change({ weights: { ...draft.weights, [key]: Number(e.target.value) } })}
                className="accent-primary w-full"
              />
            </div>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          disabled={!valid || save.isPending}
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
        {!valid && (
          <p className="text-destructive text-xs" role="alert">
            {t("cleanup.settings.invalid")}
          </p>
        )}
      </div>
    </div>
  )
}

/** Réglages du classement, repliés par défaut : la liste reste en tête. */
export function CleanupSettingsPanel() {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const { data } = useCleanupSettingsQuery()

  return (
    <div className="border-border rounded-lg border p-3">
      <button
        type="button"
        className="flex w-full items-center gap-2 text-left text-sm font-medium"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <ChevronRight className={cn("size-4 transition-transform", open && "rotate-90")} />
        {t("cleanup.settings.title")}
      </button>
      {/* Remonté après enregistrement : le brouillon repart des valeurs du serveur. */}
      {open && data && <Form key={JSON.stringify(data.settings)} data={data} />}
    </div>
  )
}
