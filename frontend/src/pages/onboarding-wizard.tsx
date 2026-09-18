import { useState } from "react"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"

import { ApiKeyServiceCard } from "@/components/settings/api-key-service-card"
import { CrossSeedCard } from "@/components/settings/cross-seed-card"
import { PathsCard } from "@/components/settings/paths-card"
import { TorrentClientCard } from "@/components/settings/torrent-client-card"
import { Button } from "@/components/ui/button"
import { useSaveSettingsMutation } from "@/hooks/use-settings"
import { useI18n, type MediaServer } from "@/i18n"
import { isCoreConfigComplete, settingsReadToForm, type SettingsRead } from "@/types/settings"

export function OnboardingWizard({ existing }: { existing: SettingsRead }) {
  const { t } = useI18n()
  const [step, setStep] = useState(0)
  const [form, setForm] = useState(() => settingsReadToForm(existing))
  const steps = [
    t("settings.sections.mediaServer"),
    "Sonarr",
    "Radarr",
    t("settings.sections.torrentClient"),
    t("settings.sections.paths"),
    "cross-seed",
  ]
  const saveSettings = useSaveSettingsMutation()

  const isLast = step === steps.length - 1
  const canFinish = isCoreConfigComplete(form, existing)

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function handleFinish() {
    try {
      await saveSettings.mutateAsync(form)
      toast.success(t("onboarding.saved"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("common.saveFailed"))
    }
  }

  return (
    <div className="mx-auto flex min-h-svh max-w-2xl flex-col justify-center px-4 py-10">
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">{t("auth.welcome")}</h1>
        <p className="text-muted-foreground mt-2">{t("onboarding.subtitle")}</p>
      </div>

      <nav className="mb-6 flex items-center justify-center gap-2 text-sm">
        {steps.map((label, i) => (
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
            {i < steps.length - 1 && <span className="bg-border h-px w-4" />}
          </div>
        ))}
      </nav>
      <p className="text-muted-foreground mb-4 text-center text-sm">
        {t("onboarding.step", { current: step + 1, total: steps.length, name: steps[step] })}
      </p>

      {step <= 2 &&
        (["emby", "sonarr", "radarr"] as const)
          .filter((_, i) => i === step)
          .map((service) => (
            <ApiKeyServiceCard
              key={service}
              service={service}
              url={form[`${service}_url`]}
              onUrlChange={(v) => set(`${service}_url`, v)}
              apiKey={form[`${service}_api_key`]}
              onApiKeyChange={(v) => set(`${service}_api_key`, v)}
              apiKeySet={existing[service].api_key_set}
              {...(service === "emby"
                ? { mediaServer: form.media_server, onMediaServerChange: (v: MediaServer) => set("media_server", v) }
                : {})}
            />
          ))}
      {step === 3 && (
        <TorrentClientCard
          client={form.torrent_client}
          onClientChange={(v) => set("torrent_client", v)}
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
          {t("common.previous")}
        </Button>

        {isLast ? (
          <Button type="button" disabled={!canFinish || saveSettings.isPending} onClick={handleFinish}>
            {saveSettings.isPending && <Loader2 className="size-4 animate-spin" />}
            {t("common.finish")}
          </Button>
        ) : (
          <Button type="button" onClick={() => setStep((s) => s + 1)}>
            {t("common.next")}
          </Button>
        )}
      </div>

      {isLast && !canFinish && (
        <p className="text-muted-foreground mt-3 text-center text-sm">{t("onboarding.incomplete")}</p>
      )}
    </div>
  )
}
