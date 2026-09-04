import { useState } from "react"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"

import { ApiKeyServiceCard } from "@/components/settings/api-key-service-card"
import { CrossSeedCard } from "@/components/settings/cross-seed-card"
import { PathsCard } from "@/components/settings/paths-card"
import { QbittorrentCard } from "@/components/settings/qbittorrent-card"
import { Button } from "@/components/ui/button"
import { useSaveSettingsMutation } from "@/hooks/use-settings"
import { isCoreConfigComplete, settingsReadToForm, type SettingsRead } from "@/types/settings"

const STEPS = ["Emby", "Sonarr", "Radarr", "qBittorrent", "Chemins", "cross-seed"] as const

export function OnboardingWizard({ existing }: { existing: SettingsRead }) {
  const [step, setStep] = useState(0)
  const [form, setForm] = useState(() => settingsReadToForm(existing))
  const saveSettings = useSaveSettingsMutation()

  const isLast = step === STEPS.length - 1
  const canFinish = isCoreConfigComplete(form, existing)

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function handleFinish() {
    try {
      await saveSettings.mutateAsync(form)
      toast.success("Configuration enregistrée.")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec de l'enregistrement.")
    }
  }

  return (
    <div className="mx-auto flex min-h-svh max-w-2xl flex-col justify-center px-4 py-10">
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Bienvenue sur Analysarr</h1>
        <p className="text-muted-foreground mt-2">
          Configurons vos services avant de commencer. Vous pourrez tout modifier plus tard depuis les Réglages.
        </p>
      </div>

      <nav className="mb-6 flex items-center justify-center gap-2 text-sm">
        {STEPS.map((label, i) => (
          <div key={label} className="flex items-center gap-2">
            <span
              className={
                "flex size-6 items-center justify-center rounded-full text-xs " +
                (i === step
                  ? "bg-primary text-primary-foreground"
                  : i < step
                    ? "bg-primary/20 text-foreground"
                    : "bg-muted text-muted-foreground")
              }
            >
              {i + 1}
            </span>
            {i < STEPS.length - 1 && <span className="bg-border h-px w-4" />}
          </div>
        ))}
      </nav>
      <p className="text-muted-foreground mb-4 text-center text-sm">
        Étape {step + 1} / {STEPS.length} — {STEPS[step]}
      </p>

      {step === 0 && (
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
      {step === 1 && (
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
      {step === 2 && (
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
      {step === 3 && (
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
      {step === 4 && (
        <PathsCard
          embyLibraryPath={form.emby_library_path}
          onEmbyLibraryPathChange={(v) => set("emby_library_path", v)}
          qbittorrentDownloadPath={form.qbittorrent_download_path}
          onQbittorrentDownloadPathChange={(v) => set("qbittorrent_download_path", v)}
        />
      )}
      {step === 5 && (
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

      <div className="mt-6 flex items-center justify-between">
        <Button type="button" variant="ghost" disabled={step === 0} onClick={() => setStep((s) => s - 1)}>
          Précédent
        </Button>

        {isLast ? (
          <Button type="button" disabled={!canFinish || saveSettings.isPending} onClick={handleFinish}>
            {saveSettings.isPending && <Loader2 className="size-4 animate-spin" />}
            Terminer
          </Button>
        ) : (
          <Button type="button" onClick={() => setStep((s) => s + 1)}>
            Suivant
          </Button>
        )}
      </div>

      {isLast && !canFinish && (
        <p className="text-muted-foreground mt-3 text-center text-sm">
          Complétez Emby, Sonarr, Radarr, qBittorrent et les chemins pour terminer (cross-seed peut rester
          désactivé).
        </p>
      )}
    </div>
  )
}
