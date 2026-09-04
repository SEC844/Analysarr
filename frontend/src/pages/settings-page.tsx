import { useState } from "react"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"

import { ApiKeyServiceCard } from "@/components/settings/api-key-service-card"
import { CrossSeedCard } from "@/components/settings/cross-seed-card"
import { PathDiagnosticsPanel } from "@/components/settings/path-diagnostics-panel"
import { PathsCard } from "@/components/settings/paths-card"
import { QbittorrentCard } from "@/components/settings/qbittorrent-card"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useSaveSettingsMutation, useSettingsQuery } from "@/hooks/use-settings"
import { settingsReadToForm, type SettingsRead } from "@/types/settings"

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
    <div className="mx-auto max-w-2xl px-4 py-10">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Réglages</h1>
        <Button type="button" onClick={handleSave} disabled={saveSettings.isPending}>
          {saveSettings.isPending && <Loader2 className="size-4 animate-spin" />}
          Enregistrer
        </Button>
      </div>

      <Tabs defaultValue="emby">
        <TabsList className="flex-wrap">
          <TabsTrigger value="emby">Emby</TabsTrigger>
          <TabsTrigger value="sonarr">Sonarr</TabsTrigger>
          <TabsTrigger value="radarr">Radarr</TabsTrigger>
          <TabsTrigger value="qbittorrent">qBittorrent</TabsTrigger>
          <TabsTrigger value="paths">Chemins</TabsTrigger>
          <TabsTrigger value="cross-seed">cross-seed</TabsTrigger>
        </TabsList>

        <TabsContent value="emby">
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
        </TabsContent>

        <TabsContent value="sonarr">
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
        </TabsContent>

        <TabsContent value="radarr">
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
        </TabsContent>

        <TabsContent value="qbittorrent">
          <QbittorrentCard
            url={form.qbittorrent_url}
            onUrlChange={(v) => set("qbittorrent_url", v)}
            username={form.qbittorrent_username}
            onUsernameChange={(v) => set("qbittorrent_username", v)}
            password={form.qbittorrent_password}
            onPasswordChange={(v) => set("qbittorrent_password", v)}
            passwordSet={existing.qbittorrent.password_set}
          />
        </TabsContent>

        <TabsContent value="paths" className="space-y-6">
          <PathsCard
            embyLibraryPath={form.emby_library_path}
            onEmbyLibraryPathChange={(v) => set("emby_library_path", v)}
            qbittorrentDownloadPath={form.qbittorrent_download_path}
            onQbittorrentDownloadPathChange={(v) => set("qbittorrent_download_path", v)}
          />
          <PathDiagnosticsPanel />
        </TabsContent>

        <TabsContent value="cross-seed">
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
        </TabsContent>
      </Tabs>
    </div>
  )
}
