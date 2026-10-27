import { Radio } from "lucide-react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { useI18n } from "@/i18n"

interface RealtimeModeCardProps {
  enabled: boolean
  onEnabledChange: (value: boolean) => void
}

/** Mode de mise à jour, en tête de la Planification : temps réel (par
 * défaut) ou scans uniquement. Enregistré avec les autres réglages. */
export function RealtimeModeCard({ enabled, onEnabledChange }: RealtimeModeCardProps) {
  const { t } = useI18n()

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <CardTitle className="flex items-center gap-2">
              <Radio className="text-muted-foreground size-4" />
              <label htmlFor="realtime-enabled">{t("realtimeMode.title")}</label>
            </CardTitle>
            <CardDescription>{t("realtimeMode.description")}</CardDescription>
          </div>
          <Switch id="realtime-enabled" checked={enabled} onCheckedChange={onEnabledChange} />
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-muted-foreground text-sm">{t(enabled ? "realtimeMode.onHint" : "realtimeMode.offHint")}</p>
      </CardContent>
    </Card>
  )
}
