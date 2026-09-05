import { useState } from "react"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"

import { AccountCard } from "@/components/settings/account-card"
import { ApiKeyServiceCard } from "@/components/settings/api-key-service-card"
import { CrossSeedCard } from "@/components/settings/cross-seed-card"
import { PathDiagnosticsPanel } from "@/components/settings/path-diagnostics-panel"
import { PathsCard } from "@/components/settings/paths-card"
import { QbittorrentCard } from "@/components/settings/qbittorrent-card"
import { ScanHistoryTable } from "@/components/settings/scan-history-table"
import { ScheduleCard } from "@/components/settings/schedule-card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useSaveSettingsMutation, useSettingsQuery } from "@/hooks/use-settings"
import { cn } from "@/lib/utils"
import { settingsReadToForm, type SettingsRead } from "@/types/settings"

const SECTION_GROUPS = [
  {
    label: "Services",
    sections: [
      { id: "emby", label: "Emby" },
      { id: "sonarr", label: "Sonarr" },
      { id: "radarr", label: "Radarr" },
      { id: "qbittorrent", label: "qBittorrent" },
      { id: "cross-seed", label: "cross-seed" },
    ],
  },
  {
    label: "Système",
    sections: [
      { id: "paths", label: "Chemins" },
      { id: "schedule", label: "Planification" },
      { id: "account", label: "Compte" },
    ],
  },
] as const

type SectionId = (typeof SECTION_GROUPS)[number]["sections"][number]["id"]

export function SettingsPage() {
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
        <p className="text-destructive">Impossible de charger les réglages.</p>
      </div>
    )
  }

  return <SettingsForm key={JSON.stringify(data)} existing={data} />
}

function SettingsForm({ existing }: { existing: SettingsRead }) {
  const [section, setSection] = useState<SectionId>("emby")
  const [form, setForm] = useState(() => settingsReadToForm(existing))
  const saveSettings = useSaveSettingsMutation()

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function handleSave() {
    try {
      await saveSettings.mutateAsync(form)
      toast.success("Réglages enregistrés.")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec de l'enregistrement.")
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-10">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Réglages</h1>
        {section !== "account" && (
          <Button type="button" onClick={handleSave} disabled={saveSettings.isPending}>
            {saveSettings.isPending && <Loader2 className="size-4 animate-spin" />}
            Enregistrer
          </Button>
        )}
      </div>

      <div className="flex flex-col gap-8 sm:flex-row">
        <nav className="flex shrink-0 flex-row gap-4 overflow-x-auto sm:w-44 sm:flex-col sm:gap-6 sm:overflow-visible">
          {SECTION_GROUPS.map((group) => (
            <div key={group.label} className="flex flex-col gap-0.5">
              <p className="text-muted-foreground px-2 pb-1 text-xs font-medium tracking-wide uppercase">
                {group.label}
              </p>
              {group.sections.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setSection(s.id)}
                  className={cn(
                    "rounded-md px-2 py-1.5 text-left text-sm whitespace-nowrap transition-colors",
                    section === s.id
                      ? "bg-secondary text-secondary-foreground"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted",
                  )}
                >
                  {s.label}
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="min-w-0 flex-1 space-y-6">
          {section === "emby" && (
            <ApiKeyServiceCard
              service="emby"
              title="Emby"
              description="Votre serveur multimédia, source de vérité pour les fichiers de la bibliothèque."
              url={form.emby_url}
              onUrlChange={(v) => set("emby_url", v)}
              urlPlaceholder="http://emby:8096"
              urlHelp="L'URL accessible depuis le conteneur Analysarr (nom du service Docker ou IP)."
              apiKey={form.emby_api_key}
              onApiKeyChange={(v) => set("emby_api_key", v)}
              apiKeyHelp="Tableau de bord Emby → Paramètres avancés → API Keys."
              apiKeySet={existing.emby.api_key_set}
            />
          )}

          {section === "sonarr" && (
            <ApiKeyServiceCard
              service="sonarr"
              title="Sonarr"
              description="Gestion des séries TV."
              url={form.sonarr_url}
              onUrlChange={(v) => set("sonarr_url", v)}
              urlPlaceholder="http://sonarr:8989"
              urlHelp="L'URL accessible depuis le conteneur Analysarr."
              apiKey={form.sonarr_api_key}
              onApiKeyChange={(v) => set("sonarr_api_key", v)}
              apiKeyHelp="Sonarr → Réglages → Général → Sécurité → Clé API."
              apiKeySet={existing.sonarr.api_key_set}
            />
          )}

          {section === "radarr" && (
            <ApiKeyServiceCard
              service="radarr"
              title="Radarr"
              description="Gestion des films."
              url={form.radarr_url}
              onUrlChange={(v) => set("radarr_url", v)}
              urlPlaceholder="http://radarr:7878"
              urlHelp="L'URL accessible depuis le conteneur Analysarr."
              apiKey={form.radarr_api_key}
              onApiKeyChange={(v) => set("radarr_api_key", v)}
              apiKeyHelp="Radarr → Réglages → Général → Sécurité → Clé API."
              apiKeySet={existing.radarr.api_key_set}
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
        </div>
      </div>
    </div>
  )
}
