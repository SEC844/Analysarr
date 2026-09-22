import { useEffect, useState } from "react"
import { Star, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useAppInfoQuery, usePreferences, useSaveAppPreferencesMutation } from "@/hooks/use-app"
import { useI18n } from "@/i18n"

// Jamais à l'arrivée : l'invitation attend que l'application ait servi
// quelques jours, puis une minute dans la session en cours. Reportée, elle ne
// revient qu'un mois plus tard ; suivie, elle ne revient jamais.
const FIRST_DELAY_DAYS = 3
const SNOOZE_DAYS = 30
const SESSION_DELAY_MS = 60_000

function daysSince(iso: string | null): number | null {
  if (!iso) return null
  const date = new Date(iso).getTime()
  if (Number.isNaN(date)) return null
  return (Date.now() - date) / 86_400_000
}

export function StarPrompt() {
  const { t, language } = useI18n()
  const { data: appInfo } = useAppInfoQuery()
  const preferences = usePreferences()
  const saveMutation = useSaveAppPreferencesMutation()
  const [sessionReady, setSessionReady] = useState(false)
  const [closed, setClosed] = useState(false)

  const { star_prompt_state: state, star_prompt_at: at } = preferences
  const elapsed = daysSince(at)

  useEffect(() => {
    const timer = setTimeout(() => setSessionReady(true), SESSION_DELAY_MS)
    return () => clearTimeout(timer)
  }, [])

  // Première ouverture : on note la date sans rien afficher, c'est elle qui
  // sert de point de départ au délai.
  useEffect(() => {
    if (!appInfo || state !== "pending" || at) return
    saveMutation.mutate({
      // La langue et l'interrupteur de mise à jour sont renvoyés tels quels :
      // cet enregistrement ne touche qu'aux préférences d'affichage.
      language: appInfo.language ?? language,
      update_check_enabled: appInfo.update_check_enabled,
      ui: { ...preferences, star_prompt_at: new Date().toISOString() },
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appInfo, state, at])

  const remember = (next: "later" | "done") => {
    setClosed(true)
    if (!appInfo) return
    saveMutation.mutate({
      language: appInfo.language ?? language,
      update_check_enabled: appInfo.update_check_enabled,
      ui: { ...preferences, star_prompt_state: next, star_prompt_at: new Date().toISOString() },
    })
  }

  const due = state === "pending" ? (elapsed ?? 0) >= FIRST_DELAY_DAYS : state === "later" && (elapsed ?? 0) >= SNOOZE_DAYS
  if (!appInfo || closed || !sessionReady || state === "done" || !due) return null

  return (
    <div
      role="status"
      className="bg-card border-border fixed right-4 bottom-4 z-50 w-[min(22rem,calc(100vw-2rem))] rounded-lg border p-4 shadow-lg"
    >
      <div className="flex items-start gap-2">
        <Star className="mt-0.5 size-4 shrink-0 text-amber-500" />
        <p className="flex-1 text-sm font-medium">{t("star.title")}</p>
        <button
          type="button"
          onClick={() => remember("later")}
          aria-label={t("star.later")}
          className="text-muted-foreground hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>
      <p className="text-muted-foreground mt-2 text-sm">{t("star.description")}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          render={
            <a href={appInfo.repository_url} target="_blank" rel="noreferrer noopener" onClick={() => remember("done")} />
          }
        >
          <Star className="size-4" />
          {t("star.action")}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => remember("later")}>
          {t("star.later")}
        </Button>
      </div>
    </div>
  )
}
