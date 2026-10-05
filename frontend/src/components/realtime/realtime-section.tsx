import { useState } from "react"
import { Loader2, MoonStar } from "lucide-react"
import { toast } from "sonner"

import { WebhooksCard } from "@/components/realtime/webhooks-card"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { PulseDot } from "@/components/ui/pulse-dot"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  useNightlyScanMutation,
  useRealtimeSettingsQuery,
  useRealtimeStatusQuery,
  useSaveRealtimeSettingsMutation,
} from "@/hooks/use-realtime"
import { useI18n } from "@/i18n"
import { formatDateTime, formatRelativeTime } from "@/lib/format"
import { sourceLabel, suggestsNightlyScan } from "@/lib/realtime"
import { cn } from "@/lib/utils"
import type { RealtimeSettings, RealtimeSettingsWrite, RealtimeStatus, SourceStatus } from "@/types/realtime"

const STATE_DOTS: Record<SourceStatus["state"], string> = {
  active: "bg-emerald-500",
  waiting: "bg-amber-500",
  error: "bg-red-500",
}

function StatusCard({ status, settings }: { status: RealtimeStatus | undefined; settings: RealtimeSettings }) {
  const { t } = useI18n()
  const sources = status?.sources ?? []
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("realtime.statusTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        {sources.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("realtime.noSource")}</p>
        ) : (
          <ul className="divide-border divide-y">
            {sources.map((source) => (
              <li key={source.key} className="flex items-start gap-3 py-2.5">
                {source.state === "error" ? (
                  <PulseDot tone="danger" label={t("realtime.states.error")} className="mt-1.5" />
                ) : (
                  <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", STATE_DOTS[source.state])} aria-hidden />
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">
                    {sourceLabel(t, source, settings.webhooks)}
                    <span className="text-muted-foreground font-normal"> · {t(`realtime.states.${source.state}`)}</span>
                  </p>
                  <p className="text-muted-foreground text-xs" title={formatDateTime(source.last_event_at) ?? undefined}>
                    {source.last_event_at
                      ? t("realtime.lastEvent", { time: formatRelativeTime(source.last_event_at) ?? "" })
                      : t("realtime.noEvent")}
                    {source.kind !== "webhook" && source.last_check_at
                      ? ` · ${t("realtime.lastCheck", { time: formatRelativeTime(source.last_check_at) ?? "" })}`
                      : ""}
                  </p>
                  {source.error && <p className="text-destructive text-xs break-words">{source.error}</p>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

// Saisie en texte : un champ vidé ne devient pas 0 en cours de frappe.
interface Draft {
  debounce: string
  torrentsEnabled: boolean
  torrentsInterval: string
  mediaServerEnabled: boolean
  mediaServerInterval: string
}

function within(value: string, [min, max]: [number, number]): number | null {
  const parsed = Number(value)
  return value.trim() !== "" && Number.isInteger(parsed) && parsed >= min && parsed <= max ? parsed : null
}

function toPayload(draft: Draft, settings: RealtimeSettings): RealtimeSettingsWrite | null {
  const debounce = within(draft.debounce, settings.debounce_bounds)
  const torrents = within(draft.torrentsInterval, settings.torrent_interval_bounds)
  const mediaServer = within(draft.mediaServerInterval, settings.media_server_interval_bounds)
  if (debounce === null || torrents === null || mediaServer === null) return null
  return {
    debounce_seconds: debounce,
    torrents_enabled: draft.torrentsEnabled,
    torrents_interval: torrents,
    media_server_enabled: draft.mediaServerEnabled,
    media_server_interval: mediaServer,
  }
}

function SecondsField({
  id,
  label,
  value,
  bounds,
  onChange,
}: {
  id: string
  label: string
  value: string
  bounds: [number, number]
  onChange: (value: string) => void
}) {
  const { t } = useI18n()
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Label htmlFor={id} className="font-normal">
        {label}
      </Label>
      <Input
        id={id}
        type="number"
        min={bounds[0]}
        max={bounds[1]}
        className="w-24"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <span className="text-muted-foreground text-sm">{t("realtime.seconds")}</span>
    </div>
  )
}

function SourcesCard({ settings }: { settings: RealtimeSettings }) {
  const { t } = useI18n()
  const save = useSaveRealtimeSettingsMutation()
  const [draft, setDraft] = useState<Draft>({
    debounce: String(settings.debounce_seconds),
    torrentsEnabled: settings.torrents_enabled,
    torrentsInterval: String(settings.torrents_interval),
    mediaServerEnabled: settings.media_server_enabled,
    mediaServerInterval: String(settings.media_server_interval),
  })
  const payload = toPayload(draft, settings)
  const update = (changes: Partial<Draft>) => setDraft((current) => ({ ...current, ...changes }))

  return (
    <Card>
      <CardContent className="space-y-6 pt-6">
        <section className="space-y-2">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium">{t("realtime.torrents.title")}</p>
              <p className="text-muted-foreground text-xs">{t("realtime.torrents.description")}</p>
            </div>
            <Switch
              checked={draft.torrentsEnabled}
              disabled={!settings.torrents_available}
              onCheckedChange={(torrentsEnabled) => update({ torrentsEnabled })}
              aria-label={t("realtime.torrents.enable")}
            />
          </div>
          {!settings.torrents_available && (
            <p className="text-muted-foreground text-xs">{t("realtime.torrents.unavailable")}</p>
          )}
          {draft.torrentsEnabled && (
            <SecondsField
              id="realtime-torrents-interval"
              label={t("realtime.interval")}
              value={draft.torrentsInterval}
              bounds={settings.torrent_interval_bounds}
              onChange={(torrentsInterval) => update({ torrentsInterval })}
            />
          )}
        </section>

        <section className="space-y-2">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium">{t("realtime.mediaServer.title")}</p>
              <p className="text-muted-foreground text-xs">{t("realtime.mediaServer.description")}</p>
            </div>
            <Switch
              checked={draft.mediaServerEnabled}
              disabled={!settings.media_server_available}
              onCheckedChange={(mediaServerEnabled) => update({ mediaServerEnabled })}
              aria-label={t("realtime.mediaServer.enable")}
            />
          </div>
          {!settings.media_server_available && (
            <p className="text-muted-foreground text-xs">{t("realtime.mediaServer.unavailable")}</p>
          )}
          {draft.mediaServerEnabled && (
            <SecondsField
              id="realtime-media-server-interval"
              label={t("realtime.interval")}
              value={draft.mediaServerInterval}
              bounds={settings.media_server_interval_bounds}
              onChange={(mediaServerInterval) => update({ mediaServerInterval })}
            />
          )}
        </section>

        <section className="space-y-1.5">
          <SecondsField
            id="realtime-debounce"
            label={t("realtime.debounce")}
            value={draft.debounce}
            bounds={settings.debounce_bounds}
            onChange={(debounce) => update({ debounce })}
          />
          <p className="text-muted-foreground text-xs">{t("realtime.debounceHelp")}</p>
        </section>

        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            disabled={!payload || save.isPending}
            onClick={() =>
              payload &&
              save.mutate(payload, {
                onSuccess: () => toast.success(t("realtime.saved")),
                onError: (err) => toast.error(err instanceof Error ? err.message : t("common.saveFailed")),
              })
            }
          >
            {save.isPending && <Loader2 className="size-4 animate-spin" />}
            {t("realtime.save")}
          </Button>
          {!payload && (
            <p className="text-destructive text-xs" role="alert">
              {t("realtime.invalid")}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

const NIGHTLY_HOUR = 4

function ReconciliationCard({ status }: { status: RealtimeStatus | undefined }) {
  const { t } = useI18n()
  const nightly = useNightlyScanMutation()
  if (!status) return null
  const { enabled, mode, interval_minutes, nightly_hour } = status.reconciliation
  const current = !enabled
    ? t("realtime.reconciliation.off")
    : mode === "nightly"
      ? t("realtime.reconciliation.nightly", { hour: nightly_hour })
      : t("realtime.reconciliation.interval", { minutes: interval_minutes ?? 0 })

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("realtime.reconciliation.title")}</CardTitle>
        <CardDescription>{t("realtime.reconciliation.description")}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center gap-3">
        <p className="text-sm">{current}</p>
        {suggestsNightlyScan(status) && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={nightly.isPending}
            onClick={() =>
              nightly.mutate(NIGHTLY_HOUR, {
                onSuccess: () => toast.success(t("realtime.reconciliation.switched")),
                onError: (err) => toast.error(err instanceof Error ? err.message : t("common.saveFailed")),
              })
            }
          >
            {nightly.isPending ? <Loader2 className="size-4 animate-spin" /> : <MoonStar className="size-4" />}
            {t("realtime.reconciliation.suggest", { hour: NIGHTLY_HOUR })}
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

/** Réglages → Configuration → Temps réel. */
export function RealtimeSection() {
  const { t } = useI18n()
  const { data: settings, isLoading } = useRealtimeSettingsQuery()
  const { data: status } = useRealtimeStatusQuery()

  if (isLoading) return <Skeleton className="h-64 w-full" />
  if (!settings) return <p className="text-destructive">{t("common.apiUnreachable")}</p>

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold">{t("realtime.title")}</h2>
        <p className="text-muted-foreground text-sm">{t("realtime.description")}</p>
      </div>
      <StatusCard status={status} settings={settings} />
      <WebhooksCard settings={settings} />
      {/* Remonté quand le serveur change les réglages : le brouillon repart d'eux. */}
      <SourcesCard key={JSON.stringify([settings.torrents_enabled, settings.media_server_enabled, settings.debounce_seconds, settings.torrents_interval, settings.media_server_interval])} settings={settings} />
      <ReconciliationCard status={status} />
    </div>
  )
}
