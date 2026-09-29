import { useState } from "react"
import { Loader2, Plus, X } from "lucide-react"
import { toast } from "sonner"

import { SeedProtectionBanner } from "@/components/seed/seed-protection-banner"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { useSaveSeedProtectionMutation, useSeedProtectionQuery } from "@/hooks/use-seed-protection"
import { useI18n } from "@/i18n"
import type { SeedProtection, SeedProtectionWrite } from "@/types/seed"

// Saisie en texte : un champ vidé ne doit pas devenir 0 en cours de frappe.
interface RuleDraft {
  key: number
  domain: string
  min_days: string
  min_ratio: string
}

interface Draft {
  enabled: boolean
  private_min_days: string
  public_enabled: boolean
  public_min_days: string
  rules: RuleDraft[]
}

let nextKey = 0

function toDraft(data: SeedProtection): Draft {
  return {
    enabled: data.enabled,
    private_min_days: String(data.private_min_days),
    public_enabled: data.public_enabled,
    public_min_days: String(data.public_min_days),
    rules: data.tracker_rules.map((rule) => ({
      key: nextKey++,
      domain: rule.domain,
      min_days: String(rule.min_days),
      min_ratio: rule.min_ratio === null ? "" : String(rule.min_ratio),
    })),
  }
}

function days(value: string, data: SeedProtection): number | null {
  const parsed = Number(value)
  return value.trim() !== "" && Number.isInteger(parsed) && parsed >= data.min_days && parsed <= data.max_days
    ? parsed
    : null
}

/** Même contrôle que le backend (bornes, domaine, doublons) : null si une
 * valeur est invalide. Le backend revérifie tout. */
function toPayload(draft: Draft, data: SeedProtection): SeedProtectionWrite | null {
  const privateDays = days(draft.private_min_days, data)
  const publicDays = days(draft.public_min_days, data)
  if (privateDays === null || publicDays === null) return null
  const rules = []
  for (const rule of draft.rules) {
    const domain = rule.domain.trim().toLowerCase().replace(/\.$/, "")
    const ruleDays = days(rule.min_days, data)
    const ratio = rule.min_ratio.trim() === "" ? null : Number(rule.min_ratio)
    const ratioValid = ratio === null || (Number.isFinite(ratio) && ratio >= 0 && ratio <= data.max_ratio)
    if (!/^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9-]{2,}$/.test(domain) || ruleDays === null || !ratioValid) {
      return null
    }
    rules.push({ domain, min_days: ruleDays, min_ratio: ratio })
  }
  if (new Set(rules.map((rule) => rule.domain)).size !== rules.length || rules.length > data.max_rules) return null
  return {
    enabled: draft.enabled,
    private_min_days: privateDays,
    public_enabled: draft.public_enabled,
    public_min_days: publicDays,
    tracker_rules: rules,
  }
}

function SeedProtectionForm({ data }: { data: SeedProtection }) {
  const { t } = useI18n()
  const saveMutation = useSaveSeedProtectionMutation()
  const [draft, setDraft] = useState<Draft>(() => toDraft(data))

  const payload = toPayload(draft, data)
  const update = (changes: Partial<Draft>) => setDraft((current) => ({ ...current, ...changes }))
  const updateRule = (key: number, changes: Partial<RuleDraft>) =>
    update({ rules: draft.rules.map((rule) => (rule.key === key ? { ...rule, ...changes } : rule)) })

  const save = () => {
    if (!payload) return
    saveMutation.mutate(payload, {
      onSuccess: (saved) => {
        setDraft(toDraft(saved))
        toast.success(t("seed.saved"))
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : t("common.saveFailed")),
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("seed.title")}</CardTitle>
        <CardDescription>{t("seed.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="flex items-center justify-between gap-4">
          <Label htmlFor="seed-enabled" className="font-normal">
            {t("seed.enabled")}
          </Label>
          <Switch id="seed-enabled" checked={draft.enabled} onCheckedChange={(enabled) => update({ enabled })} />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="seed-private-days">{t("seed.privateDays")}</Label>
          <div className="flex items-center gap-2">
            <Input
              id="seed-private-days"
              type="number"
              min={data.min_days}
              max={data.max_days}
              className="w-28"
              value={draft.private_min_days}
              onChange={(e) => update({ private_min_days: e.target.value })}
            />
            <span className="text-muted-foreground text-sm">{t("seed.days")}</span>
          </div>
          <p className="text-muted-foreground text-xs">{t("seed.privateHelp")}</p>
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="seed-public-enabled" className="font-normal">
              {t("seed.publicEnabled")}
            </Label>
            <Switch
              id="seed-public-enabled"
              checked={draft.public_enabled}
              onCheckedChange={(public_enabled) => update({ public_enabled })}
            />
          </div>
          {draft.public_enabled && (
            <div className="space-y-1.5">
              <Label htmlFor="seed-public-days">{t("seed.publicDays")}</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="seed-public-days"
                  type="number"
                  min={data.min_days}
                  max={data.max_days}
                  className="w-28"
                  value={draft.public_min_days}
                  onChange={(e) => update({ public_min_days: e.target.value })}
                />
                <span className="text-muted-foreground text-sm">{t("seed.days")}</span>
              </div>
            </div>
          )}
          <p className="text-muted-foreground text-xs">{t("seed.referenceHelp")}</p>
        </div>

        <div className="space-y-3">
          <div>
            <p className="text-sm font-medium">{t("seed.rulesTitle")}</p>
            <p className="text-muted-foreground text-xs">{t("seed.rulesDescription")}</p>
          </div>
          {draft.rules.length === 0 && <p className="text-muted-foreground text-sm">{t("seed.noRules")}</p>}
          <datalist id="seed-known-trackers">
            {data.known_trackers.map((domain) => (
              <option key={domain} value={domain} />
            ))}
          </datalist>
          <ul className="space-y-2">
            {draft.rules.map((rule) => (
              <li key={rule.key} className="flex flex-wrap items-end gap-2">
                <div className="min-w-0 flex-1 basis-48 space-y-1">
                  <Label htmlFor={`seed-rule-domain-${rule.key}`} className="text-xs">
                    {t("seed.domain")}
                  </Label>
                  <Input
                    id={`seed-rule-domain-${rule.key}`}
                    list="seed-known-trackers"
                    placeholder={t("seed.domainPlaceholder")}
                    value={rule.domain}
                    onChange={(e) => updateRule(rule.key, { domain: e.target.value })}
                  />
                </div>
                <div className="w-24 space-y-1">
                  <Label htmlFor={`seed-rule-days-${rule.key}`} className="text-xs">
                    {t("seed.days")}
                  </Label>
                  <Input
                    id={`seed-rule-days-${rule.key}`}
                    type="number"
                    min={data.min_days}
                    max={data.max_days}
                    value={rule.min_days}
                    onChange={(e) => updateRule(rule.key, { min_days: e.target.value })}
                  />
                </div>
                <div className="w-28 space-y-1">
                  <Label htmlFor={`seed-rule-ratio-${rule.key}`} className="text-xs">
                    {t("seed.ratio")}
                  </Label>
                  <Input
                    id={`seed-rule-ratio-${rule.key}`}
                    type="number"
                    step="0.1"
                    min={0}
                    max={data.max_ratio}
                    placeholder={t("seed.ratioPlaceholder")}
                    value={rule.min_ratio}
                    onChange={(e) => updateRule(rule.key, { min_ratio: e.target.value })}
                  />
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={t("seed.removeRule", { domain: rule.domain })}
                  onClick={() => update({ rules: draft.rules.filter((other) => other.key !== rule.key) })}
                >
                  <X className="size-4" />
                </Button>
              </li>
            ))}
          </ul>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={draft.rules.length >= data.max_rules}
            onClick={() =>
              update({
                rules: [
                  ...draft.rules,
                  { key: nextKey++, domain: "", min_days: draft.private_min_days, min_ratio: "" },
                ],
              })
            }
          >
            <Plus className="size-4" />
            {t("seed.addRule")}
          </Button>
        </div>

        <p className="text-muted-foreground text-xs">{t("seed.manualHelp")}</p>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" disabled={!payload || saveMutation.isPending} onClick={save}>
            {saveMutation.isPending && <Loader2 className="size-4 animate-spin" />}
            {t("seed.save")}
          </Button>
          {!payload && (
            <p className="text-destructive text-xs" role="alert">
              {t("seed.invalid", { min: data.min_days, max: data.max_days, ratio: data.max_ratio })}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export function SeedProtectionSection() {
  const { t } = useI18n()
  const { data, isLoading } = useSeedProtectionQuery()

  if (isLoading) return <Skeleton className="h-64 w-full" />
  if (!data) return <p className="text-destructive">{t("common.apiUnreachable")}</p>

  return (
    <div className="space-y-4">
      <SeedProtectionBanner withSettingsLink={false} />
      {/* Remonté quand le serveur change l'état (bandeau accepté/refusé). */}
      <SeedProtectionForm key={`${data.enabled}-${data.prompt}`} data={data} />
    </div>
  )
}
