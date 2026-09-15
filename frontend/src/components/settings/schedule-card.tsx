import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { useI18n } from "@/i18n"

const PRESETS = [
  { value: "60", label: "schedule.presets.hourly" },
  { value: "180", label: "schedule.presets.every3h" },
  { value: "360", label: "schedule.presets.every6h" },
  { value: "720", label: "schedule.presets.every12h" },
  { value: "1440", label: "schedule.presets.daily" },
  { value: "custom", label: "schedule.presets.custom" },
] as const

const PRESET_VALUES = new Set<string>(PRESETS.map((p) => p.value).filter((v) => v !== "custom"))

interface ScheduleCardProps {
  enabled: boolean
  onEnabledChange: (value: boolean) => void
  intervalMinutes: number | null
  onIntervalMinutesChange: (value: number | null) => void
}

export function ScheduleCard({ enabled, onEnabledChange, intervalMinutes, onIntervalMinutesChange }: ScheduleCardProps) {
  const { t } = useI18n()
  const presetLabels: Record<string, string> = Object.fromEntries(PRESETS.map((p) => [p.value, t(p.label)]))
  const currentValue =
    intervalMinutes != null ? (PRESET_VALUES.has(String(intervalMinutes)) ? String(intervalMinutes) : "custom") : "60"
  const isCustom = currentValue === "custom"

  function handleEnabledChange(value: boolean) {
    onEnabledChange(value)
    // Active la planification avec une fréquence concrète dès le départ
    // (jamais un intervalle vide enregistré silencieusement).
    if (value && intervalMinutes == null) {
      onIntervalMinutesChange(60)
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-4">
          <div>
            <CardTitle>{t("schedule.title")}</CardTitle>
            <CardDescription>{t("schedule.description")}</CardDescription>
          </div>
          <Switch id="schedule-enabled" checked={enabled} onCheckedChange={handleEnabledChange} />
        </div>
      </CardHeader>
      {enabled && (
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="schedule-interval">{t("schedule.frequency")}</Label>
            <div className="flex flex-wrap items-center gap-2">
              <Select
                value={currentValue}
                onValueChange={(v) => onIntervalMinutesChange(v === "custom" ? (intervalMinutes ?? 60) : Number(v))}
              >
                <SelectTrigger id="schedule-interval" className="w-56">
                  <SelectValue placeholder={t("schedule.frequency")}>{(v: string) => presetLabels[v] ?? v}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {PRESETS.map((p) => (
                    <SelectItem key={p.value} value={p.value}>
                      {presetLabels[p.value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {isCustom && (
                <div className="flex items-center gap-1.5">
                  <Input
                    type="number"
                    min={5}
                    className="w-24"
                    value={intervalMinutes ?? ""}
                    onChange={(e) => onIntervalMinutesChange(e.target.value ? Number(e.target.value) : null)}
                  />
                  <span className="text-muted-foreground text-sm">{t("schedule.minutes")}</span>
                </div>
              )}
            </div>
            <p className="text-muted-foreground text-sm">{t("schedule.hint")}</p>
          </div>
        </CardContent>
      )}
    </Card>
  )
}
