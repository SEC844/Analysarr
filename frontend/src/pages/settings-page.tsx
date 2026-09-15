import { useState } from "react"
import { Loader2 } from "lucide-react"
import { useSearchParams } from "react-router-dom"
import { toast } from "sonner"

import { AccountCard } from "@/components/settings/account-card"
import { ApiKeyServiceCard } from "@/components/settings/api-key-service-card"
import { ApplicationSection } from "@/components/settings/application-section"
import { CrossSeedCard } from "@/components/settings/cross-seed-card"
import { EmbyUsersCard } from "@/components/settings/emby-users-card"
import { PathDiagnosticsPanel } from "@/components/settings/path-diagnostics-panel"
import { PathsCard } from "@/components/settings/paths-card"
import { QbittorrentCard } from "@/components/settings/qbittorrent-card"
import { ScanHistoryTable } from "@/components/settings/scan-history-table"
import { ScheduleCard } from "@/components/settings/schedule-card"
import { Button } from "@/components/ui/button"
import { PulseDot } from "@/components/ui/pulse-dot"
import { Skeleton } from "@/components/ui/skeleton"
import { useAppInfoQuery } from "@/hooks/use-app"
import { useSaveSettingsMutation, useSettingsQuery } from "@/hooks/use-settings"
import { useI18n, type MessageKey } from "@/i18n"
import { cn } from "@/lib/utils"
import { settingsReadToForm, type SettingsRead } from "@/types/settings"

// Noms de services : jamais traduits. Sections système : clés de traduction.
const SECTION_GROUPS = [
  {
    label: "settings.groups.services",
    sections: [
      { id: "emby", label: "Emby" },
      { id: "sonarr", label: "Sonarr" },
      { id: "radarr", label: "Radarr" },
      { id: "qbittorrent", label: "qBittorrent" },
      { id: "cross-seed", label: "cross-seed" },
    ],
  },
  {
    label: "settings.groups.system",
    sections: [
      { id: "paths", label: "settings.sections.paths" },
      { id: "schedule", label: "settings.sections.schedule" },
      { id: "account", label: "settings.sections.account" },
      { id: "application", label: "settings.sections.application" },
    ],
  },
] as const

type SectionId = (typeof SECTION_GROUPS)[number]["sections"][number]["id"]

const SECTION_IDS = new Set<string>(SECTION_GROUPS.flatMap((g) => g.sections.map((s) => s.id)))
// Sections qui enregistrent elles-mêmes leurs changements (pas de bouton global).
const SELF_SAVING_SECTIONS = new Set<SectionId>(["account", "application"])

export function SettingsPage() {
  const { t } = useI18n()
  const { data, isLoading, isError } = useSettingsQuery()

  if (isLoading) {
    return (
      <div className="mx-auto max-w-2xl space-y-4 px-4 py-10">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-10">
        <p className="text-destructive">{t("settings.loadFailed")}</p>
      </div>
    )
  }

  return <SettingsForm key={JSON.stringify(data)} existing={data} />
}

function SettingsForm({ existing }: { existing: SettingsRead }) {
  const { t } = useI18n()
  // Section dans l'URL : lien direct possible (pastille de mise à jour →
  // onglet Application) et conservée quand l'interface change de langue.
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = searchParams.get("section")
  const section: SectionId = requested && SECTION_IDS.has(requested) ? (requested as SectionId) : "emby"
  const setSection = (id: SectionId) => setSearchParams({ section: id }, { replace: true })

  const [form, setForm] = useState(() => settingsReadToForm(existing))
  const saveSettings = useSaveSettingsMutation()
  const { data: appInfo } = useAppInfoQuery()
  const updateAvailable = appInfo?.update?.update_available ?? false

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function handleSave() {
    try {
      await saveSettings.mutateAsync(form)
      toast.success(t("settings.saved"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("common.saveFailed"))
    }
  }

  const label = (value: string) => (value.startsWith("settings.") ? t(value as MessageKey) : value)

  return (
    <div className="mx-auto max-w-5xl px-4 py-10">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">{t("settings.title")}</h1>
        {!SELF_SAVING_SECTIONS.has(section) && (
          <Button type="button" onClick={handleSave} disabled={saveSettings.isPending}>
            {saveSettings.isPending && <Loader2 className="size-4 animate-spin" />}
            {t("common.save")}
          </Button>
        )}
      </div>

      <div className="flex flex-col gap-8 sm:flex-row">
        <nav className="flex shrink-0 flex-row gap-4 overflow-x-auto sm:w-44 sm:flex-col sm:gap-6 sm:overflow-visible">
          {SECTION_GROUPS.map((group) => (
            <div key={group.label} className="flex flex-col gap-0.5">
              <p className="text-muted-foreground px-2 pb-1 text-xs font-medium tracking-wide uppercase">
                {label(group.label)}
              </p>
              {group.sections.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setSection(s.id)}
                  className={cn(
                    "flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm whitespace-nowrap transition-colors",
                    section === s.id
                      ? "bg-secondary text-secondary-foreground"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted",
                  )}
                >
                  {label(s.label)}
                  {s.id === "application" && updateAvailable && <PulseDot label={t("nav.updateAvailable")} />}
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="min-w-0 flex-1 space-y-6">
          {(section === "emby" || section === "sonarr" || section === "radarr") && (
            <ApiKeyServiceCard
              key={section}
              service={section}
              url={form[`${section}_url`]}
              onUrlChange={(v) => set(`${section}_url`, v)}
              apiKey={form[`${section}_api_key`]}
              onApiKeyChange={(v) => set(`${section}_api_key`, v)}
              apiKeySet={existing[section].api_key_set}
            />
          )}

          {section === "emby" && existing.emby.api_key_set && (
            <EmbyUsersCard
              excluded={form.excluded_emby_user_ids}
              onExcludedChange={(v) => set("excluded_emby_user_ids", v)}
            />
          )}

          {section === "qbittorrent" && (
            <QbittorrentCard
              url={form.qbittorrent_url}
              onUrlChange={(v) => set("qbittorrent_url", v)}
              username={form.qbittorrent_username}
              onUsernameChange={(v) => set("qbittorrent_username", v)}
              password={form.qbittorrent_password}
              onPasswordChange={(v) => set("qbittorrent_password", v)}
              passwordSet={existing.qbittorrent.password_set}
            />
          )}

          {section === "cross-seed" && (
            <CrossSeedCard
              enabled={form.cross_seed_enabled}
              onEnabledChange={(v) => set("cross_seed_enabled", v)}
              url={form.cross_seed_url}
              onUrlChange={(v) => set("cross_seed_url", v)}
              apiKey={form.cross_seed_api_key}
              onApiKeyChange={(v) => set("cross_seed_api_key", v)}
              apiKeySet={existing.cross_seed.api_key_set}
              libraryPath={form.cross_seed_library_path}
              onLibraryPathChange={(v) => set("cross_seed_library_path", v)}
            />
          )}

          {section === "paths" && (
            <div className="space-y-6">
              <PathsCard
                embyLibraryPath={form.emby_library_path}
                onEmbyLibraryPathChange={(v) => set("emby_library_path", v)}
                qbittorrentDownloadPath={form.qbittorrent_download_path}
                onQbittorrentDownloadPathChange={(v) => set("qbittorrent_download_path", v)}
              />
              <PathDiagnosticsPanel />
            </div>
          )}

          {section === "schedule" && (
            <div className="space-y-6">
              <ScheduleCard
                enabled={form.scan_schedule_enabled}
                onEnabledChange={(v) => set("scan_schedule_enabled", v)}
                intervalMinutes={form.scan_schedule_interval_minutes}
                onIntervalMinutesChange={(v) => set("scan_schedule_interval_minutes", v)}
              />
              <ScanHistoryTable />
            </div>
          )}

          {section === "account" && <AccountCard />}

          {section === "application" && <ApplicationSection />}
        </div>
      </div>
    </div>
  )
}
