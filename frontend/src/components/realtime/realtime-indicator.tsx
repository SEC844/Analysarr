import { Link } from "react-router-dom"
import { Radio } from "lucide-react"

import { PulseDot } from "@/components/ui/pulse-dot"
import { useRealtimeStatusQuery } from "@/hooks/use-realtime"
import { useI18n } from "@/i18n"
import { realtimeSummary } from "@/lib/realtime"

/** À côté du bouton Scanner : rien tant que le temps réel est inactif, une
 * pastille verte quand il suit la bibliothèque, rouge clignotante dès qu'une
 * source est en erreur. Mène à Réglages → Temps réel. */
export function RealtimeIndicator() {
  const { t } = useI18n()
  const { data } = useRealtimeStatusQuery()
  const summary = realtimeSummary(data)
  if (summary === "off") return null

  const label = summary === "error" ? t("realtime.indicator.error") : t("realtime.indicator.active")
  return (
    <Link
      to="/settings?section=realtime"
      title={t("realtime.indicator.open")}
      className="border-border text-muted-foreground hover:text-foreground inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-sm transition-colors"
    >
      <Radio className="size-4" />
      <span className="hidden sm:inline">{t("realtime.indicator.active")}</span>
      {summary === "error" ? (
        <PulseDot tone="danger" label={label} />
      ) : (
        <span className="size-2 rounded-full bg-emerald-500" role="img" aria-label={label} />
      )}
    </Link>
  )
}
