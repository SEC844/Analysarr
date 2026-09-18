import { useState } from "react"
import { Check, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { ApiKeyServiceCard } from "@/components/settings/api-key-service-card"
import { CrossSeedCard } from "@/components/settings/cross-seed-card"
import { PathsCard } from "@/components/settings/paths-card"
import { SeerCard } from "@/components/settings/seer-card"
import { TorrentClientCard } from "@/components/settings/torrent-client-card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Logo } from "@/components/ui/logo"
import { useSaveSettingsMutation } from "@/hooks/use-settings"
import { useI18n, type MediaServer } from "@/i18n"
import { cn } from "@/lib/utils"
import {
  TORRENT_CLIENT_NAMES,
  missingCoreConfig,
  settingsReadToForm,
  type CoreStep,
  type SettingsRead,
  type SettingsWrite,
} from "@/types/settings"

// Une étape par service, dans l'ordre de configuration. `core` : étape
// obligatoire pour terminer (les autres services restent facultatifs, comme
// dans les Réglages).
type StepId = CoreStep | "crossSeed" | "seer" | "summary"

const STEPS: { id: StepId; core: boolean }[] = [
  { id: "mediaServer", core: true },
  { id: "sonarr", core: true },
  { id: "radarr", core: true },
  { id: "torrentClient", core: true },
  { id: "paths", core: true },
  { id: "crossSeed", core: false },
  { id: "seer", core: false },
  { id: "summary", core: false },
]

const API_KEY_SERVICES = { mediaServer: "emby", sonarr: "sonarr", radarr: "radarr" } as const

export function OnboardingWizard({ existing }: { existing: SettingsRead }) {
  const { t } = useI18n()
  const [step, setStep] = useState(0)
  const [form, setForm] = useState(() => settingsReadToForm(existing))
  const saveSettings = useSaveSettingsMutation()

  const missing = missingCoreConfig(form, existing)
  const canFinish = missing.length === 0
  const current = STEPS[step]
  const isLast = step === STEPS.length - 1
  const clientName = TORRENT_CLIENT_NAMES[form.torrent_client]
  // Les trois services à clé API partagent la même carte.
  const apiKeyService = API_KEY_SERVICES[current.id as keyof typeof API_KEY_SERVICES] ?? null

  function stepLabel(id: StepId): string {
    if (id === "sonarr") return "Sonarr"
    if (id === "radarr") return "Radarr"
    if (id === "crossSeed") return "cross-seed"
    if (id === "seer") return "Seer"
    if (id === "summary") return t("onboarding.summary.step")
    if (id === "paths") return t("settings.sections.paths")
    if (id === "torrentClient") return t("settings.sections.torrentClient")
    return t("settings.sections.mediaServer")
  }

  function set<K extends keyof SettingsWrite>(key: K, value: SettingsWrite[K]) {
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
        <Logo className="mx-auto mb-3 size-10" />
        <h1 className="text-3xl font-semibold tracking-tight">{t("auth.welcome")}</h1>
        <p className="text-muted-foreground mt-2">{t("onboarding.subtitle")}</p>
      </div>

      {/* Chaque étape est atteignable directement : rien n'est bloquant, le
          récapitulatif final rappelle ce qui manque. */}
      <nav className="mb-4 flex items-center justify-center gap-2">
        {STEPS.map((s, i) => {
          const done = s.core ? !missing.includes(s.id as CoreStep) : true
          return (
            <div key={s.id} className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setStep(i)}
                aria-label={stepLabel(s.id)}
                aria-current={i === step ? "step" : undefined}
                className={cn(
                  "flex size-6 items-center justify-center rounded-full text-xs transition-colors",
                  i === step
                    ? "bg-primary text-primary-foreground"
                    : s.core && done
                      ? "bg-primary/20 text-foreground"
                      : "bg-muted text-muted-foreground hover:bg-muted/70",
                )}
              >
                {s.core && done && i !== step ? <Check className="size-3.5" /> : i + 1}
              </button>
              {i < STEPS.length - 1 && <span className="bg-border h-px w-4" />}
            </div>
          )
        })}
      </nav>
      <p className="text-muted-foreground mb-4 flex items-center justify-center gap-2 text-center text-sm">
        {t("onboarding.step", { current: step + 1, total: STEPS.length, name: stepLabel(current.id) })}
        {!current.core && current.id !== "summary" && <Badge variant="outline">{t("common.optional")}</Badge>}
      </p>

      {apiKeyService !== null && (
        <ApiKeyServiceCard
          service={apiKeyService}
          url={form[`${apiKeyService}_url`]}
          onUrlChange={(v) => set(`${apiKeyService}_url`, v)}
          apiKey={form[`${apiKeyService}_api_key`]}
          onApiKeyChange={(v) => set(`${apiKeyService}_api_key`, v)}
          apiKeySet={existing[apiKeyService].api_key_set}
          {...(apiKeyService === "emby"
            ? { mediaServer: form.media_server, onMediaServerChange: (v: MediaServer) => set("media_server", v) }
            : {})}
        />
      )}

      {current.id === "torrentClient" && (
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

      {current.id === "paths" && (
        <PathsCard
          torrentClientName={clientName}
          embyLibraryPath={form.emby_library_path}
          onEmbyLibraryPathChange={(v) => set("emby_library_path", v)}
          qbittorrentDownloadPath={form.qbittorrent_download_path}
          onQbittorrentDownloadPathChange={(v) => set("qbittorrent_download_path", v)}
        />
      )}

      {current.id === "crossSeed" && (
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

      {current.id === "seer" && (
        <SeerCard
          enabled={form.seer_enabled}
          onEnabledChange={(v) => set("seer_enabled", v)}
          url={form.seer_url}
          onUrlChange={(v) => set("seer_url", v)}
          apiKey={form.seer_api_key}
          onApiKeyChange={(v) => set("seer_api_key", v)}
          apiKeySet={existing.seer.api_key_set}
        />
      )}

      {current.id === "summary" && (
        <SummaryCard
          form={form}
          existing={existing}
          missing={missing}
          onGoToStep={(id) => setStep(STEPS.findIndex((s) => s.id === id))}
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
    </div>
  )
}

interface SummaryCardProps {
  form: SettingsWrite
  existing: SettingsRead
  missing: CoreStep[]
  onGoToStep: (id: StepId) => void
}

/** Dernier écran : ce qui est prêt, ce qui manque, et ce qui vient après. */
function SummaryCard({ form, existing, missing, onGoToStep }: SummaryCardProps) {
  const { t } = useI18n()
  const clientName = TORRENT_CLIENT_NAMES[form.torrent_client]

  const rows: { id: StepId; name: string; state: "ready" | "missing" | "off" }[] = [
    {
      id: "mediaServer",
      name: form.media_server === "jellyfin" ? "Jellyfin" : "Emby",
      state: missing.includes("mediaServer") ? "missing" : "ready",
    },
    { id: "sonarr", name: "Sonarr", state: missing.includes("sonarr") ? "missing" : "ready" },
    { id: "radarr", name: "Radarr", state: missing.includes("radarr") ? "missing" : "ready" },
    {
      id: "torrentClient",
      name: clientName,
      state: missing.includes("torrentClient") ? "missing" : "ready",
    },
    {
      id: "paths",
      name: t("settings.sections.paths"),
      state: missing.includes("paths") ? "missing" : "ready",
    },
    {
      id: "crossSeed",
      name: "cross-seed",
      state: form.cross_seed_enabled && (form.cross_seed_url || existing.cross_seed.url) ? "ready" : "off",
    },
    {
      id: "seer",
      name: "Seer",
      state: form.seer_enabled && (form.seer_url || existing.seer.url) ? "ready" : "off",
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("onboarding.summary.title")}</CardTitle>
        <CardDescription>
          {missing.length === 0 ? t("onboarding.summary.ready") : t("onboarding.summary.incomplete")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <ul className="divide-border divide-y text-sm">
          {rows.map((row) => (
            <li key={row.id} className="flex items-center justify-between gap-3 py-2">
              <span className="min-w-0 truncate">{row.name}</span>
              {row.state === "ready" ? (
                <Badge variant="secondary">
                  <Check className="size-3.5" />
                  {t("onboarding.summary.configured")}
                </Badge>
              ) : row.state === "off" ? (
                <Badge variant="outline">{t("onboarding.summary.disabled")}</Badge>
              ) : (
                <Button type="button" variant="ghost" size="sm" onClick={() => onGoToStep(row.id)}>
                  {t("onboarding.summary.toComplete")}
                </Button>
              )}
            </li>
          ))}
        </ul>
        <p className="text-muted-foreground text-sm">{t("onboarding.summary.afterFinish")}</p>
      </CardContent>
    </Card>
  )
}
