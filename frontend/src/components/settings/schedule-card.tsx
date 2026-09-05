import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"

const PRESETS = [
  { value: "60", label: "Toutes les heures" },
  { value: "180", label: "Toutes les 3 heures" },
  { value: "360", label: "Toutes les 6 heures" },
  { value: "720", label: "Toutes les 12 heures" },
  { value: "1440", label: "Une fois par jour" },
  { value: "custom", label: "Personnalisé" },
]

const PRESET_VALUES = new Set(PRESETS.map((p) => p.value).filter((v) => v !== "custom"))
const PRESET_LABELS: Record<string, string> = Object.fromEntries(PRESETS.map((p) => [p.value, p.label]))

interface ScheduleCardProps {
  enabled: boolean
  onEnabledChange: (value: boolean) => void
  intervalMinutes: number | null
  onIntervalMinutesChange: (value: number | null) => void
}

export function ScheduleCard({ enabled, onEnabledChange, intervalMinutes, onIntervalMinutesChange }: ScheduleCardProps) {
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
            <CardTitle>Planification</CardTitle>
            <CardDescription>Lance un scan automatiquement à intervalle régulier, sans intervention.</CardDescription>
          </div>
          <Switch id="schedule-enabled" checked={enabled} onCheckedChange={handleEnabledChange} />
        </div>
      </CardHeader>
      {enabled && (
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="schedule-interval">Fréquence</Label>
            <div className="flex flex-wrap items-center gap-2">
              <Select
                value={currentValue}
                onValueChange={(v) => onIntervalMinutesChange(v === "custom" ? (intervalMinutes ?? 60) : Number(v))}
              >
                <SelectTrigger id="schedule-interval" className="w-56">
                  <SelectValue placeholder="Fréquence">{(v: string) => PRESET_LABELS[v] ?? v}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {PRESETS.map((p) => (
                    <SelectItem key={p.value} value={p.value}>
                      {p.label}
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
                  <span className="text-muted-foreground text-sm">minutes</span>
                </div>
              )}
            </div>
            <p className="text-muted-foreground text-sm">
              Un scan déjà en cours (manuel ou planifié) n'est jamais interrompu ni dupliqué.
            </p>
          </div>
        </CardContent>
      )}
    </Card>
  )
}
