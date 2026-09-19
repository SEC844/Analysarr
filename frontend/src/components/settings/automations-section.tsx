import { useState } from "react"
import { ChevronRight, Loader2, Play, Plus, Trash2, Wand2 } from "lucide-react"
import { toast } from "sonner"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  useAutomationsQuery,
  useCreateAutomationMutation,
  useDeleteAutomationMutation,
  usePreviewAutomationMutation,
  useRunAutomationMutation,
  useUpdateAutomationMutation,
} from "@/hooks/use-automations"
import { useI18n, type MessageKey } from "@/i18n"
import { formatBytes, formatDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import {
  AUTOMATION_TRIGGERS,
  type Automation,
  type AutomationAction,
  type AutomationRunResult,
  type AutomationTrigger,
  type AutomationWrite,
} from "@/types/automations"

const GIGABYTE = 1024 ** 3
// La réparation des hardlinks n'a de sens que sur les torrents non hardlinkés
// (même règle côté backend).
const ACTIONS_BY_TRIGGER: Record<AutomationTrigger, AutomationAction[]> = {
  orphan_detected: ["cleanup", "cross_seed_search", "notify_only"],
  duplicate_detected: ["cleanup", "notify_only"],
  non_hardlink_detected: ["repair_hardlinks", "cross_seed_search", "notify_only"],
  // Un import bloqué ne se règle ni par suppression ni par hardlink :
  // la seule action utile est de redemander l'import.
  import_failed_detected: ["retry_import", "notify_only"],
  // Analysarr ne touche jamais à un téléchargement en cours : seule la
  // notification a du sens ici (le nettoyage de file est le rôle de
  // Cleanuparr ou Decluttarr).
  stalled_download_detected: ["notify_only"],
}

const emptyRule = (): AutomationWrite => ({
  name: "",
  enabled: true,
  trigger: "orphan_detected",
  action: "cleanup",
  conditions: { media_types: [] },
  max_actions: 5,
  // Nouvelle règle en simulation : on regarde ce qu'elle ferait avant de la
  // laisser agir.
  dry_run: true,
})

const toWrite = (automation: Automation): AutomationWrite => ({
  name: automation.name,
  enabled: automation.enabled,
  trigger: automation.trigger,
  action: automation.action,
  conditions: automation.conditions,
  max_actions: automation.max_actions,
  dry_run: automation.dry_run,
})

function numberOrNull(value: string): number | null {
  const parsed = Number(value)
  return value.trim() === "" || Number.isNaN(parsed) ? null : parsed
}

function RunSummary({ result }: { result: AutomationRunResult }) {
  const { t } = useI18n()
  return (
    <Alert className="mt-3">
      <Wand2 className="size-4" />
      <AlertTitle>
        {result.dry_run ? t("automations.previewTitle") : t("automations.runTitle")}
        {" · "}
        {t("automations.matched", { count: result.matched })}
      </AlertTitle>
      <AlertDescription className="space-y-1">
        {result.freed_bytes > 0 && <p>{t("automations.freed", { size: formatBytes(result.freed_bytes) })}</p>}
        <ul className="space-y-0.5">
          {result.steps.slice(0, 10).map((step, index) => (
            <li key={`${step.label}-${index}`} className={step.success ? "" : "text-destructive"}>
              {step.label}
              {step.error && ` — ${step.error}`}
            </li>
          ))}
        </ul>
        {result.steps.length === 0 && <p>{t("automations.nothingToDo")}</p>}
      </AlertDescription>
    </Alert>
  )
}

interface AutomationCardProps {
  automation?: Automation
  onDone?: () => void
}

function AutomationCard({ automation, onDone }: AutomationCardProps) {
  const { t } = useI18n()
  // Règle existante repliée : la liste reste lisible même avec dix règles.
  const [open, setOpen] = useState(!automation)
  const [rule, setRule] = useState<AutomationWrite>(automation ? toWrite(automation) : emptyRule())
  const [result, setResult] = useState<AutomationRunResult | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [confirmRun, setConfirmRun] = useState(false)

  const create = useCreateAutomationMutation()
  const update = useUpdateAutomationMutation()
  const remove = useDeleteAutomationMutation()
  const preview = usePreviewAutomationMutation()
  const run = useRunAutomationMutation()

  const onError = (err: unknown) => toast.error(err instanceof Error ? err.message : t("common.saveFailed"))
  const set = <K extends keyof AutomationWrite>(key: K, value: AutomationWrite[K]) =>
    setRule((current) => ({ ...current, [key]: value }))
  const setCondition = <K extends keyof AutomationWrite["conditions"]>(
    key: K,
    value: AutomationWrite["conditions"][K],
  ) => setRule((current) => ({ ...current, conditions: { ...current.conditions, [key]: value } }))

  const actions = ACTIONS_BY_TRIGGER[rule.trigger]
  const saving = create.isPending || update.isPending

  function handleSave() {
    const payload = { ...rule, name: rule.name.trim() }
    if (automation) {
      update.mutate({ id: automation.id, payload }, { onSuccess: () => toast.success(t("automations.saved")), onError })
      return
    }
    create.mutate(payload, {
      onSuccess: () => {
        toast.success(t("automations.saved"))
        onDone?.()
      },
      onError,
    })
  }

  function handleRun() {
    if (!automation) return
    if (!rule.dry_run && !confirmRun) {
      setConfirmRun(true)
      return
    }
    run.mutate(automation.id, {
      onSuccess: (data) => {
        setResult(data)
        toast.success(t("automations.ran", { count: data.executed }))
      },
      onError,
      onSettled: () => setConfirmRun(false),
    })
  }

  function handleDelete() {
    if (!automation) return
    if (!confirmDelete) {
      setConfirmDelete(true)
      return
    }
    remove.mutate(automation.id, { onError, onSettled: () => setConfirmDelete(false) })
  }

  return (
    <Card>
      <CardHeader className={cn(!open && "pb-4")}>
        <div className="flex items-center justify-between gap-4">
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            className="flex min-w-0 flex-1 items-center gap-3 text-left"
          >
            <ChevronRight className={cn("text-muted-foreground size-4 shrink-0 transition-transform", open && "rotate-90")} />
            <span className="min-w-0">
              <CardTitle className="truncate">{automation ? automation.name : t("automations.newRule")}</CardTitle>
              <CardDescription className="truncate">
                {t(`automations.triggers.${rule.trigger}` as MessageKey)} → {t(`automations.actions.${rule.action}` as MessageKey)}
              </CardDescription>
            </span>
          </button>
          <div className="flex shrink-0 items-center gap-2">
            {rule.dry_run && <Badge variant="outline">{t("automations.dryRun")}</Badge>}
            <Switch checked={rule.enabled} onCheckedChange={(v) => set("enabled", v)} aria-label={t("automations.enabled")} />
          </div>
        </div>
      </CardHeader>
      {open && (
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor={`rule-name-${automation?.id ?? "new"}`}>{t("automations.name")}</Label>
            <Input
              id={`rule-name-${automation?.id ?? "new"}`}
              value={rule.name}
              maxLength={40}
              placeholder={t("automations.namePlaceholder")}
              onChange={(e) => set("name", e.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`rule-max-${automation?.id ?? "new"}`}>{t("automations.maxActions")}</Label>
            <Input
              id={`rule-max-${automation?.id ?? "new"}`}
              type="number"
              min={1}
              max={50}
              value={rule.max_actions}
              onChange={(e) => set("max_actions", Math.min(50, Math.max(1, Number(e.target.value) || 1)))}
            />
            <p className="text-muted-foreground text-sm">{t("automations.maxActionsHelp")}</p>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor={`rule-trigger-${automation?.id ?? "new"}`}>{t("automations.trigger")}</Label>
            <Select
              value={rule.trigger}
              onValueChange={(value) => {
                const trigger = value as AutomationTrigger
                const allowed = ACTIONS_BY_TRIGGER[trigger]
                setRule((current) => ({
                  ...current,
                  trigger,
                  action: allowed.includes(current.action) ? current.action : allowed[0],
                }))
              }}
            >
              <SelectTrigger id={`rule-trigger-${automation?.id ?? "new"}`}>
                <SelectValue>{(v: string) => t(`automations.triggers.${v}` as MessageKey)}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {AUTOMATION_TRIGGERS.map((trigger) => (
                  <SelectItem key={trigger} value={trigger}>
                    {t(`automations.triggers.${trigger}` as MessageKey)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`rule-action-${automation?.id ?? "new"}`}>{t("automations.action")}</Label>
            <Select value={rule.action} onValueChange={(value) => set("action", value as AutomationAction)}>
              <SelectTrigger id={`rule-action-${automation?.id ?? "new"}`}>
                <SelectValue>{(v: string) => t(`automations.actions.${v}` as MessageKey)}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {actions.map((action) => (
                  <SelectItem key={action} value={action}>
                    {t(`automations.actions.${action}` as MessageKey)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-sm">{t(`automations.actionHelp.${rule.action}` as MessageKey)}</p>
          </div>
        </div>

        <div className="space-y-3 border-t pt-4">
          <p className="text-sm font-medium">{t("automations.conditions")}</p>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor={`rule-seed-${automation?.id ?? "new"}`}>{t("automations.minSeedDays")}</Label>
              <Input
                id={`rule-seed-${automation?.id ?? "new"}`}
                type="number"
                min={0}
                placeholder={t("automations.noCondition")}
                value={rule.conditions.min_seed_days ?? ""}
                onChange={(e) => setCondition("min_seed_days", numberOrNull(e.target.value))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`rule-ratio-${automation?.id ?? "new"}`}>{t("automations.minRatio")}</Label>
              <Input
                id={`rule-ratio-${automation?.id ?? "new"}`}
                type="number"
                min={0}
                step="0.1"
                placeholder={t("automations.noCondition")}
                value={rule.conditions.min_ratio ?? ""}
                onChange={(e) => setCondition("min_ratio", numberOrNull(e.target.value))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`rule-size-${automation?.id ?? "new"}`}>{t("automations.minSize")}</Label>
              <Input
                id={`rule-size-${automation?.id ?? "new"}`}
                type="number"
                min={0}
                step="0.5"
                placeholder={t("automations.noCondition")}
                value={rule.conditions.min_reclaimable_bytes ? rule.conditions.min_reclaimable_bytes / GIGABYTE : ""}
                onChange={(e) => {
                  const gigabytes = numberOrNull(e.target.value)
                  setCondition("min_reclaimable_bytes", gigabytes === null ? null : Math.round(gigabytes * GIGABYTE))
                }}
              />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-4">
            {(["movie", "series"] as const).map((mediaType) => (
              <label key={mediaType} className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={rule.conditions.media_types.includes(mediaType)}
                  onCheckedChange={(checked) =>
                    setCondition(
                      "media_types",
                      checked === true
                        ? [...rule.conditions.media_types, mediaType]
                        : rule.conditions.media_types.filter((m) => m !== mediaType),
                    )
                  }
                />
                {t(mediaType === "movie" ? "filters.movies" : "filters.series")}
              </label>
            ))}
            <span className="text-muted-foreground text-sm">{t("automations.mediaTypesHelp")}</span>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={rule.dry_run} onCheckedChange={(v) => set("dry_run", v)} />
            {t("automations.dryRunLabel")}
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t pt-4">
          <Button type="button" disabled={!rule.name.trim() || saving} onClick={handleSave}>
            {saving && <Loader2 className="size-4 animate-spin" />}
            {t("common.save")}
          </Button>
          {automation && (
            <>
              <Button
                type="button"
                variant="secondary"
                disabled={preview.isPending}
                onClick={() =>
                  preview.mutate(automation.id, { onSuccess: setResult, onError })
                }
              >
                {preview.isPending && <Loader2 className="size-4 animate-spin" />}
                {t("automations.preview")}
              </Button>
              <Button
                type="button"
                variant={confirmRun ? "destructive" : "outline"}
                disabled={run.isPending}
                onClick={handleRun}
                onBlur={() => setConfirmRun(false)}
              >
                {run.isPending ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
                {confirmRun ? t("automations.confirmRun") : t("automations.run")}
              </Button>
              <Button
                type="button"
                variant={confirmDelete ? "destructive" : "ghost"}
                disabled={remove.isPending}
                onClick={handleDelete}
                onBlur={() => setConfirmDelete(false)}
              >
                <Trash2 className="size-4" />
                {confirmDelete ? t("automations.confirmDelete") : t("automations.delete")}
              </Button>
            </>
          )}
          {!automation && (
            <Button type="button" variant="ghost" onClick={onDone}>
              {t("common.cancel")}
            </Button>
          )}
        </div>

        {automation?.last_run_at && (
          <p className="text-muted-foreground text-sm">
            {t("automations.lastRun", {
              date: formatDateTime(automation.last_run_at) ?? "—",
              count: automation.last_run_count,
            })}
          </p>
        )}
        {result && <RunSummary result={result} />}
      </CardContent>
      )}
    </Card>
  )
}

export function AutomationsSection() {
  const { t } = useI18n()
  const { data: automations, isLoading } = useAutomationsQuery()
  const [adding, setAdding] = useState(false)

  if (isLoading) return <Skeleton className="h-64 w-full" />
  if (!automations) return <p className="text-destructive">{t("common.apiUnreachable")}</p>

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardTitle>{t("automations.title")}</CardTitle>
              <CardDescription>{t("automations.description")}</CardDescription>
            </div>
            <Button type="button" variant="outline" size="sm" onClick={() => setAdding(true)} disabled={adding}>
              <Plus className="size-4" />
              {t("automations.add")}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">{t("automations.safety")}</p>
        </CardContent>
      </Card>

      {automations.map((automation) => (
        <AutomationCard key={automation.id} automation={automation} />
      ))}
      {adding && <AutomationCard onDone={() => setAdding(false)} />}
      {!adding && automations.length === 0 && <p className="text-muted-foreground text-sm">{t("automations.empty")}</p>}
    </div>
  )
}
