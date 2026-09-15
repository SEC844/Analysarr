import type { ReactNode } from "react"
import { useTheme } from "next-themes"
import { toast } from "sonner"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { useAppInfoQuery, useSaveAppPreferencesMutation } from "@/hooks/use-app"
import { LANGUAGES, useI18n, type Language, type MessageKey } from "@/i18n"
import type { AppInfo, GridSizePreference, UiPreferences } from "@/types/app"
import type { MediaSort } from "@/types/media"

const THEMES = ["dark", "light", "system"] as const

const SORT_OPTIONS: [MediaSort, MessageKey][] = [
  ["title", "filters.sortTitle"],
  ["year", "filters.sortYear"],
  ["size", "filters.sortSize"],
  ["last_played", "watch.sortLastPlayed"],
  ["cleanup", "watch.sortCleanup"],
]
const GRID_OPTIONS: [GridSizePreference, MessageKey][] = [
  ["large", "grid.large"],
  ["medium", "grid.medium"],
  ["small", "grid.small"],
]

export function SettingRow({ id, label, help, children }: { id: string; label: string; help?: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <div className="space-y-0.5">
        <Label htmlFor={id}>{label}</Label>
        {help && <p className="text-muted-foreground text-sm">{help}</p>}
      </div>
      {children}
    </div>
  )
}

function OptionSelect<T extends string>({
  id,
  value,
  options,
  onChange,
}: {
  id: string
  value: T
  options: [T, MessageKey][]
  onChange: (value: T) => void
}) {
  const { t } = useI18n()
  const labels = Object.fromEntries(options.map(([option, key]) => [option, t(key)]))
  return (
    <Select value={value} onValueChange={(v) => onChange(v as T)}>
      <SelectTrigger id={id} className="w-52 shrink-0">
        <SelectValue>{(v: string) => labels[v] ?? v}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        {options.map(([option]) => (
          <SelectItem key={option} value={option}>
            {labels[option]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function AppearanceCard({ info }: { info: AppInfo }) {
  const { t, language, setLanguage } = useI18n()
  const { theme, setTheme } = useTheme()
  const savePreferences = useSaveAppPreferencesMutation()
  const languageLabels = Object.fromEntries(LANGUAGES.map((l) => [l.value, l.label]))

  const handleLanguageChange = (next: Language) => {
    savePreferences.mutate(
      { language: next, update_check_enabled: info.update_check_enabled },
      {
        // Appliquée seulement une fois enregistrée : changer de langue remonte
        // toute l'interface (voir I18nProvider).
        onSuccess: () => setLanguage(next),
        onError: (err) => toast.error(err instanceof Error ? err.message : t("common.saveFailed")),
      },
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("preferences.appearanceTitle")}</CardTitle>
        <CardDescription>{t("preferences.appearanceDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="divide-border divide-y">
        <SettingRow id="app-language" label={t("application.language")} help={t("application.languageHelp")}>
          <Select value={language} onValueChange={(v) => handleLanguageChange(v as Language)}>
            <SelectTrigger id="app-language" className="w-52 shrink-0" disabled={savePreferences.isPending}>
              <SelectValue>{(v: string) => languageLabels[v] ?? v}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {LANGUAGES.map((l) => (
                <SelectItem key={l.value} value={l.value}>
                  {l.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingRow>
        <SettingRow id="app-theme" label={t("application.theme")} help={t("application.themeHelp")}>
          <OptionSelect
            id="app-theme"
            value={(theme ?? "dark") as (typeof THEMES)[number]}
            options={THEMES.map((value) => [value, `application.themes.${value}` as MessageKey])}
            onChange={setTheme}
          />
        </SettingRow>
      </CardContent>
    </Card>
  )
}

export function PreferencesSection({ seerEnabled }: { seerEnabled: boolean }) {
  const { t, language } = useI18n()
  const { data: info, isLoading } = useAppInfoQuery()
  const savePreferences = useSaveAppPreferencesMutation()

  if (isLoading) return <Skeleton className="h-64 w-full" />
  if (!info) return <p className="text-destructive">{t("common.apiUnreachable")}</p>

  // Enregistrement immédiat à chaque changement (affichage mis à jour aussitôt).
  const update = <K extends keyof UiPreferences>(key: K, value: UiPreferences[K]) =>
    savePreferences.mutate(
      { language, update_check_enabled: info.update_check_enabled, ui: { ...info.ui, [key]: value } },
      { onError: (err) => toast.error(err instanceof Error ? err.message : t("common.saveFailed")) },
    )

  const toggle = (key: keyof UiPreferences, label: MessageKey, help: MessageKey) => (
    <SettingRow key={key} id={`pref-${key}`} label={t(label)} help={t(help)}>
      <Switch id={`pref-${key}`} checked={Boolean(info.ui[key])} onCheckedChange={(checked) => update(key, checked)} />
    </SettingRow>
  )

  return (
    <div className="space-y-6">
      <AppearanceCard info={info} />

      <Card>
        <CardHeader>
          <CardTitle>{t("preferences.libraryTitle")}</CardTitle>
          <CardDescription>{t("preferences.libraryDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="divide-border divide-y">
          {toggle("card_show_watch", "preferences.cardWatch", "preferences.cardWatchHelp")}
          {toggle("card_show_total_size", "preferences.cardTotalSize", "preferences.cardTotalSizeHelp")}
          {toggle("card_show_reclaimable", "preferences.cardReclaimable", "preferences.cardReclaimableHelp")}
          {seerEnabled &&
            toggle("card_show_requested_by", "preferences.cardRequestedBy", "preferences.cardRequestedByHelp")}
          <SettingRow id="pref-sort" label={t("preferences.defaultSort")}>
            <OptionSelect
              id="pref-sort"
              value={info.ui.library_default_sort}
              options={SORT_OPTIONS}
              onChange={(v) => update("library_default_sort", v)}
            />
          </SettingRow>
          <SettingRow id="pref-grid" label={t("preferences.defaultGrid")}>
            <OptionSelect
              id="pref-grid"
              value={info.ui.library_default_grid}
              options={GRID_OPTIONS}
              onChange={(v) => update("library_default_grid", v)}
            />
          </SettingRow>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("preferences.mediaTitle")}</CardTitle>
          <CardDescription>{t("preferences.mediaDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="divide-border divide-y">
          {toggle("media_sections_expanded", "preferences.sectionsExpanded", "preferences.sectionsExpandedHelp")}
          {toggle("delete_remove_from_arr_default", "preferences.removeArrDefault", "preferences.removeArrDefaultHelp")}
          {toggle("absolute_dates", "preferences.absoluteDates", "preferences.absoluteDatesHelp")}
        </CardContent>
      </Card>
    </div>
  )
}
