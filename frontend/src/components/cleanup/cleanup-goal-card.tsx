import { useState, type FormEvent } from "react"
import { Heart, Loader2, Target } from "lucide-react"
import { toast } from "sonner"

import { UserAvatar } from "@/components/media/watch-stats"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useCleanupPlanMutation, useForecastQuery } from "@/hooks/use-cleanup"
import { useI18n } from "@/i18n"
import { amountToBytes } from "@/lib/cleanup"
import { dayFromToday, formatDay } from "@/lib/forecast"
import { formatBytes } from "@/lib/format"
import type { CleanupCandidate, CleanupPlan, CleanupPlanRequest, PlanLossMedia } from "@/types/cleanup"
import type { DiskForecast } from "@/types/forecast"

type Goal = CleanupPlanRequest["goal"]

// Borne du serveur (schemas/cleanup.py : un an au plus).
const MAX_DAYS = 365

function LossLine({ media }: { media: PlanLossMedia }) {
  const { t } = useI18n()
  const reasons = [
    media.in_progress &&
      (media.media_type === "series"
        ? t("cleanup.goal.inProgressSeries", { count: Math.round(media.progress) })
        : t("cleanup.goal.inProgressMovie", { progress: Math.round(media.progress) })),
    media.favorite && t("cleanup.goal.favorite"),
  ].filter(Boolean)
  return (
    <li className="flex items-center gap-1.5">
      {/* Emplacement fixe : les titres restent alignés, cœur ou non. */}
      <span className="flex size-3 shrink-0">
        {media.favorite && <Heart className="size-3 fill-current text-rose-500" aria-hidden />}
      </span>
      <span className="truncate">{media.title}</span>
      <span className="text-muted-foreground shrink-0">· {reasons.join(", ")}</span>
    </li>
  )
}

function PlanResult({ plan, until, onSelect }: { plan: CleanupPlan; until: string; onSelect: () => void }) {
  const { t } = useI18n()
  const enough = plan.goal === "until" && plan.target_bytes === 0

  return (
    <div className="space-y-3 border-t pt-3" role="status">
      {enough ? (
        <p className="text-sm">{t("cleanup.goal.enough", { date: formatDay(until, true) })}</p>
      ) : (
        <>
          {plan.goal === "until" && plan.free_bytes !== null && plan.growth_per_day !== null && (
            <p className="text-muted-foreground text-xs">
              {t("cleanup.goal.untilContext", {
                free: formatBytes(plan.free_bytes),
                growth: formatBytes(plan.growth_per_day),
                target: formatBytes(plan.target_bytes),
                date: formatDay(until, true),
              })}
            </p>
          )}
          <p className="text-sm font-medium">
            {t("cleanup.goal.result", { count: plan.items.length, size: formatBytes(plan.freed_bytes) })}
          </p>
          {plan.shortfall_bytes > 0 && (
            <p className="text-sm text-amber-700 dark:text-amber-400">
              {t("cleanup.goal.shortfall", { size: formatBytes(plan.shortfall_bytes) })}
            </p>
          )}
          {plan.limited && (
            <p className="text-sm text-amber-700 dark:text-amber-400">
              {t("cleanup.goal.limited", { max: plan.max_items })}
            </p>
          )}
          {plan.items.length > 0 && (
            <>
              <ul className="max-h-48 space-y-1 overflow-y-auto text-sm">
                {plan.items.map((item) => (
                  <li key={item.media_id} className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate">
                      {item.title}
                      {item.year ? <span className="text-muted-foreground"> ({item.year})</span> : null}
                    </span>
                    <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                      {formatBytes(item.reclaimable_bytes)}
                    </span>
                  </li>
                ))}
              </ul>
              <div className="space-y-2">
                <h3 className="text-sm font-medium">{t("cleanup.goal.losses")}</h3>
                {plan.losses.length === 0 ? (
                  <p className="text-muted-foreground text-xs">{t("cleanup.goal.lossesNone")}</p>
                ) : (
                  <>
                    <p className="text-muted-foreground text-xs">{t("cleanup.goal.lossesHint")}</p>
                    <ul className="space-y-2">
                      {plan.losses.map((loss) => (
                        <li key={loss.user_id} className="flex items-start gap-2">
                          <UserAvatar user={{ id: loss.user_id, name: loss.name, image_tag: loss.image_tag }} />
                          <div className="min-w-0 flex-1 text-xs">
                            <p className="text-sm font-medium">{loss.name}</p>
                            <ul className="space-y-0.5">
                              {loss.media.map((media) => (
                                <LossLine key={media.media_id} media={media} />
                              ))}
                            </ul>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
              <Button type="button" size="sm" onClick={onSelect}>
                {t("cleanup.goal.select")}
              </Button>
            </>
          )}
        </>
      )}
    </div>
  )
}

/** Disques dont la prévision permet le mode « tenir jusqu'au ». */
function forecastable(disks: DiskForecast[] | undefined): DiskForecast[] {
  return (disks ?? []).filter((disk) => disk.trend !== null)
}

/** Mode objectif : simulation (rien n'est supprimé), puis la sélection
 * proposée remplit celle de l'assistant — la suppression passe par son
 * dialogue habituel. */
export function CleanupGoalCard({ onSelect }: { onSelect: (items: CleanupCandidate[]) => void }) {
  const { t } = useI18n()
  const forecast = useForecastQuery()
  const plan = useCleanupPlanMutation()
  const disks = forecastable(forecast.data?.disks)
  const [goal, setGoal] = useState<Goal>("free")
  const [amount, setAmount] = useState("100")
  const [until, setUntil] = useState(() => dayFromToday(30))
  const [diskKey, setDiskKey] = useState<string | null>(null)
  const disk = disks.find((d) => d.key === diskKey) ?? disks[0]
  const roles = (d: DiskForecast) => d.roles.map((role) => t(`forecast.roles.${role}`)).join(" · ")

  const bytes = amountToBytes(amount)
  const dateValid = until > dayFromToday(0) && until <= dayFromToday(MAX_DAYS)
  const valid = goal === "free" ? bytes !== null : disk !== undefined && dateValid

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!valid) return
    const payload: CleanupPlanRequest =
      goal === "free" || !disk
        ? { goal: "free", target_bytes: bytes ?? 0 }
        : { goal: "until", until, disk: disk.key }
    plan.mutate(payload, {
      onError: (error) => toast.error(t("cleanup.goal.failed", { error: error.message })),
    })
  }

  const select = () => {
    if (!plan.data) return
    onSelect(plan.data.items)
    toast.success(t("cleanup.goal.selected"))
  }

  return (
    <section aria-labelledby="cleanup-goal-title" className="border-border space-y-3 rounded-lg border p-4">
      <div className="space-y-1">
        <h2 id="cleanup-goal-title" className="flex items-center gap-2 text-sm font-medium">
          <Target className="text-muted-foreground size-4" />
          {t("cleanup.goal.title")}
        </h2>
        <p className="text-muted-foreground text-xs">{t("cleanup.goal.description")}</p>
      </div>
      <form className="space-y-3" onSubmit={submit}>
        <div className="flex flex-wrap gap-2" role="group" aria-label={t("cleanup.goal.title")}>
          {(["free", "until"] as const).map((value) => (
            <Button
              key={value}
              type="button"
              size="sm"
              variant={goal === value ? "default" : "outline"}
              aria-pressed={goal === value}
              onClick={() => {
                setGoal(value)
                plan.reset()
              }}
            >
              {t(`cleanup.goal.${value}`)}
            </Button>
          ))}
        </div>
        {goal === "free" ? (
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label htmlFor="cleanup-goal-amount">{t("cleanup.goal.amount")}</Label>
              <div className="flex items-center gap-1.5">
                <Input
                  id="cleanup-goal-amount"
                  inputMode="decimal"
                  className="w-28"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  aria-invalid={bytes === null}
                />
                <span className="text-muted-foreground text-sm">{t("cleanup.goal.unit")}</span>
              </div>
            </div>
          </div>
        ) : disks.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            {t("cleanup.goal.untilUnavailable", { min: forecast.data?.min_history_days ?? 14 })}
          </p>
        ) : (
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <Label htmlFor="cleanup-goal-date">{t("cleanup.goal.date")}</Label>
              <Input
                id="cleanup-goal-date"
                type="date"
                className="w-44"
                min={dayFromToday(1)}
                max={dayFromToday(MAX_DAYS)}
                value={until}
                onChange={(e) => setUntil(e.target.value)}
                aria-invalid={!dateValid}
              />
            </div>
            {disks.length > 1 && disk && (
              <div className="space-y-1">
                <Label>{t("cleanup.goal.disk")}</Label>
                <Select value={disk.key} onValueChange={(value) => setDiskKey(value)}>
                  <SelectTrigger className="w-60" aria-label={t("cleanup.goal.disk")}>
                    <SelectValue>{(value: string) => roles(disks.find((d) => d.key === value) ?? disk)}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {disks.map((d) => (
                      <SelectItem key={d.key} value={d.key}>
                        {roles(d)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
        )}
        {goal === "free" && bytes === null && (
          <p className="text-destructive text-xs">{t("cleanup.goal.invalidAmount")}</p>
        )}
        {goal === "until" && disks.length > 0 && !dateValid && (
          <p className="text-destructive text-xs">{t("cleanup.goal.invalidDate")}</p>
        )}
        <Button type="submit" size="sm" variant="outline" disabled={!valid || plan.isPending}>
          {plan.isPending && <Loader2 className="size-4 animate-spin" />}
          {t("cleanup.goal.simulate")}
        </Button>
      </form>
      {plan.data && (
        <PlanResult
          plan={plan.data}
          until={plan.variables?.goal === "until" ? plan.variables.until : until}
          onSelect={select}
        />
      )}
    </section>
  )
}
