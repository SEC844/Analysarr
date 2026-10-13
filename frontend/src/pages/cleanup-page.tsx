import { useSearchParams } from "react-router-dom"
import { HardDrive, Trash2 } from "lucide-react"

import { CleanupAssistant } from "@/components/cleanup/cleanup-assistant"
import { ForecastSection } from "@/components/forecast/forecast-section"
import { SideNavLayout } from "@/components/layout/side-nav"
import { useI18n } from "@/i18n"

type Section = "assistant" | "space"

/** Page Nettoyage : l'assistant de suppression (par défaut) et l'espace
 * disque avec ses prévisions, chacun dans sa section. La section vit dans
 * l'URL (`?section=space`) à côté des filtres de l'assistant, qu'elle ne
 * touche pas : passer d'une section à l'autre les conserve. */
export function CleanupPage() {
  const { t } = useI18n()
  const [params, setParams] = useSearchParams()
  const section: Section = params.get("section") === "space" ? "space" : "assistant"
  const setSection = (id: Section) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current)
        if (id === "assistant") next.delete("section")
        else next.set("section", id)
        return next
      },
      { replace: true },
    )

  return (
    <div className="mx-auto max-w-[1600px] px-4 py-8 sm:px-6">
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">{t("nav.cleanup")}</h1>
      <SideNavLayout
        label={t("nav.cleanup")}
        breakpoint="lg"
        value={section}
        onChange={setSection}
        groups={[
          {
            items: [
              { id: "assistant", label: t("cleanup.title"), icon: Trash2 },
              { id: "space", label: t("cleanup.spaceSection"), icon: HardDrive },
            ],
          },
        ]}
      >
        {section === "assistant" ? <CleanupAssistant /> : <ForecastSection />}
      </SideNavLayout>
    </div>
  )
}
