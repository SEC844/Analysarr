import type { ReactNode } from "react"
import { Loader2, Send } from "lucide-react"
import { toast } from "sonner"

import { SettingRow } from "@/components/settings/preferences-section"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { useTestNotificationsMutation } from "@/hooks/use-history"
import { useI18n } from "@/i18n"
import type { NotificationChannel, NotificationsRead, SettingsWrite } from "@/types/settings"

// Noms de services : jamais traduits.
const CHANNEL_NAMES: Record<NotificationChannel, string> = { discord: "Discord", ntfy: "ntfy", gotify: "Gotify" }

interface NotificationsSectionProps {
  form: SettingsWrite
  onChange: <K extends keyof SettingsWrite>(key: K, value: SettingsWrite[K]) => void
  status: NotificationsRead
}

function TextField({
  id,
  label,
  help,
  value,
  onChange,
  placeholder,
  secret = false,
}: {
  id: string
  label: string
  help?: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  secret?: boolean
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type={secret ? "password" : "text"}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
      />
      {help && <p className="text-muted-foreground text-sm">{help}</p>}
    </div>
  )
}

export function NotificationsSection({ form, onChange, status }: NotificationsSectionProps) {
  const { t } = useI18n()
  const test = useTestNotificationsMutation()

  const configured: Record<NotificationChannel, boolean> = {
    discord: status.discord_set,
    ntfy: status.ntfy_set,
    gotify: Boolean(status.gotify_url && status.gotify_token_set),
  }
  const hasSavedChannel = Object.values(configured).some(Boolean)
  const secretPlaceholder = (set: boolean, fallback: string) => (set ? t("common.keepSecretPlaceholder") : fallback)

  const toggleRemoval = (channel: NotificationChannel) =>
    onChange(
      "notify_clear",
      form.notify_clear.includes(channel)
        ? form.notify_clear.filter((c) => c !== channel)
        : [...form.notify_clear, channel],
    )

  const channel = (id: NotificationChannel, fields: ReactNode) => {
    const pendingRemoval = form.notify_clear.includes(id)
    return (
      <div className="space-y-3 py-4 first:pt-0 last:pb-0">
        <div className="flex items-center justify-between gap-4">
          <p className="flex items-center gap-2 text-sm font-medium">
            {CHANNEL_NAMES[id]}
            {configured[id] && !pendingRemoval && <Badge variant="secondary">{t("notifications.configured")}</Badge>}
          </p>
          {configured[id] && (
            <Button type="button" variant="ghost" size="sm" onClick={() => toggleRemoval(id)}>
              {pendingRemoval ? t("common.cancel") : t("notifications.remove")}
            </Button>
          )}
        </div>
        {pendingRemoval ? <p className="text-muted-foreground text-sm">{t("notifications.willBeRemoved")}</p> : fields}
      </div>
    )
  }

  function handleTest() {
    test.mutate(undefined, {
      onSuccess: ({ results }) => {
        const entries = Object.entries(results) as [NotificationChannel, string | null][]
        const failures = entries.filter(([, error]) => error)
        if (failures.length === 0) {
          toast.success(t("notifications.testSuccess", { channels: entries.map(([c]) => CHANNEL_NAMES[c]).join(", ") }))
        } else {
          toast.error(
            t("notifications.testFailed", {
              details: failures.map(([c, error]) => `${CHANNEL_NAMES[c]} (${error})`).join(", "),
            }),
          )
        }
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : t("notifications.testError")),
    })
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {t("notifications.title")}
            <Badge variant="outline">{t("common.optional")}</Badge>
          </CardTitle>
          <CardDescription>{t("notifications.description")}</CardDescription>
        </CardHeader>
        <CardContent className="divide-border divide-y">
          {channel(
            "discord",
            <TextField
              id="notify-discord"
              label={t("notifications.discordWebhook")}
              help={t("notifications.discordHelp")}
              value={form.notify_discord_webhook}
              onChange={(v) => onChange("notify_discord_webhook", v)}
              placeholder={secretPlaceholder(status.discord_set, "https://discord.com/api/webhooks/…")}
              secret
            />,
          )}
          {channel(
            "ntfy",
            <>
              <TextField
                id="notify-ntfy-url"
                label={t("notifications.ntfyUrl")}
                help={t("notifications.ntfyHelp")}
                value={form.notify_ntfy_url}
                onChange={(v) => onChange("notify_ntfy_url", v)}
                placeholder={secretPlaceholder(status.ntfy_set, "https://ntfy.sh/…")}
                secret
              />
              <TextField
                id="notify-ntfy-token"
                label={t("notifications.ntfyToken")}
                value={form.notify_ntfy_token}
                onChange={(v) => onChange("notify_ntfy_token", v)}
                placeholder={secretPlaceholder(status.ntfy_token_set, "tk_…")}
                secret
              />
            </>,
          )}
          {channel(
            "gotify",
            <>
              <TextField
                id="notify-gotify-url"
                label={t("notifications.gotifyUrl")}
                value={form.notify_gotify_url}
                onChange={(v) => onChange("notify_gotify_url", v)}
                placeholder="http://gotify:80"
              />
              <TextField
                id="notify-gotify-token"
                label={t("notifications.gotifyToken")}
                help={t("notifications.gotifyHelp")}
                value={form.notify_gotify_token}
                onChange={(v) => onChange("notify_gotify_token", v)}
                placeholder={secretPlaceholder(status.gotify_token_set, t("notifications.gotifyToken"))}
                secret
              />
            </>,
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("notifications.eventsTitle")}</CardTitle>
          <CardDescription>{t("notifications.eventsDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="divide-border divide-y">
          <SettingRow id="notify-on-scan" label={t("notifications.onScan")} help={t("notifications.onScanHelp")}>
            <Switch id="notify-on-scan" checked={form.notify_on_scan} onCheckedChange={(v) => onChange("notify_on_scan", v)} />
          </SettingRow>
          <SettingRow
            id="notify-on-scan-failure"
            label={t("notifications.onScanFailure")}
            help={t("notifications.onScanFailureHelp")}
          >
            <Switch
              id="notify-on-scan-failure"
              checked={form.notify_on_scan_failure}
              onCheckedChange={(v) => onChange("notify_on_scan_failure", v)}
            />
          </SettingRow>
          <SettingRow id="notify-on-actions" label={t("notifications.onActions")} help={t("notifications.onActionsHelp")}>
            <Switch
              id="notify-on-actions"
              checked={form.notify_on_actions}
              onCheckedChange={(v) => onChange("notify_on_actions", v)}
            />
          </SettingRow>
          <div className="flex flex-wrap items-center gap-3 pt-4">
            <Button type="button" variant="secondary" disabled={test.isPending || !hasSavedChannel} onClick={handleTest}>
              {test.isPending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
              {t("notifications.test")}
            </Button>
            <p className="text-muted-foreground text-sm">{t("notifications.testHint")}</p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
