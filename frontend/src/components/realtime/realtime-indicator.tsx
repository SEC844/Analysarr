import { Link } from "react-router-dom"
import { Radio } from "lucide-react"

import { PulseDot } from "@/components/ui/pulse-dot"
import { useRealtimeStatusQuery } from "@/hooks/use-realtime"
import { useI18n } from "@/i18n"
import { failingSource, realtimeSummary, sourceSection } from "@/lib/realtime"

const PILL = "border-border text-muted-foreground inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-sm"

/** À côté du bouton Scanner : pastille verte quand vos services sont suivis
 * en continu ; rouge clignotante dès qu'une source est en erreur, et le lien
 * mène alors droit à la section du service concerné. */
export function RealtimeIndicator() {
  const { t } = useI18n()
  const { data } = useRealtimeStatusQuery()
  const summary = realtimeSummary(data)
  if (summary === "off") return null

  const failing = failingSource(data)
  if (!failing) {
    return (
      <span className={PILL} title={t("realtime.indicator.ok")}>
        <Radio className="size-4" />
        <span className="hidden sm:inline">{t("realtime.indicator.active")}</span>
        <span className="size-2 rounded-full bg-emerald-500" role="img" aria-label={t("realtime.indicator.ok")} />
      </span>
    )
  }
  return (
    <Link
      to={`/settings?section=${sourceSection(failing)}`}
      title={failing.error ?? t("realtime.indicator.error")}
      className={`${PILL} hover:text-foreground transition-colors`}
    >
      <Radio className="size-4" />
      <span className="hidden sm:inline">{t("realtime.indicator.active")}</span>
      <PulseDot tone="danger" label={t("realtime.indicator.error")} />
    </Link>
  )
}
