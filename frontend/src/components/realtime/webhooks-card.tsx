import { useState } from "react"
import { Loader2, PlugZap, Unplug, Zap } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  usePreviewWebhookMutation,
  useRegisterWebhookMutation,
  useTestWebhookMutation,
  useUnregisterWebhookMutation,
} from "@/hooks/use-realtime"
import { useI18n } from "@/i18n"
import { defaultAnalysarrUrl } from "@/lib/realtime"
import type { RealtimeSettings, WebhookPreview, WebhookRead } from "@/types/realtime"

function failed(err: unknown, fallback: string) {
  toast.error(err instanceof Error ? err.message : fallback)
}

/** Aperçu de ce qui sera créé chez Sonarr/Radarr, puis confirmation. */
function ConnectDialog({
  hook,
  preview,
  url,
  onClose,
}: {
  hook: WebhookRead
  preview: WebhookPreview | null
  url: string
  onClose: () => void
}) {
  const { t } = useI18n()
  const register = useRegisterWebhookMutation()

  return (
    <Dialog open={preview !== null} onOpenChange={(open) => !open && !register.isPending && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("realtime.webhooks.previewTitle", { name: hook.name })}</DialogTitle>
          <DialogDescription>{t("realtime.webhooks.previewDescription", { name: hook.name })}</DialogDescription>
        </DialogHeader>
        {preview && (
          <dl className="min-w-0 space-y-3 text-sm">
            <div>
              <dt className="text-muted-foreground text-xs">{t("realtime.webhooks.previewUrl")}</dt>
              <dd className="font-mono text-xs break-all">{preview.url}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs">{t("realtime.webhooks.previewEvents")}</dt>
              <dd className="flex flex-wrap gap-1 pt-1">
                {preview.events.map((event) => (
                  <Badge key={event} variant="secondary" className="font-mono text-[11px]">
                    {event}
                  </Badge>
                ))}
              </dd>
            </div>
          </dl>
        )}
        <DialogFooter>
          <Button
            type="button"
            disabled={register.isPending}
            onClick={() =>
              register.mutate(
                { service: hook.service, instanceId: hook.instance_id, url },
                {
                  onSuccess: () => {
                    toast.success(t("realtime.webhooks.done", { name: hook.name }))
                    onClose()
                  },
                  onError: (err) => failed(err, t("common.saveFailed")),
                },
              )
            }
          >
            {register.isPending ? <Loader2 className="size-4 animate-spin" /> : <PlugZap className="size-4" />}
            {t("realtime.webhooks.confirm")}
          </Button>
          <Button type="button" variant="outline" disabled={register.isPending} onClick={onClose}>
            {t("common.cancel")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function WebhookRow({ hook, url }: { hook: WebhookRead; url: string }) {
  const { t } = useI18n()
  const preview = usePreviewWebhookMutation()
  const test = useTestWebhookMutation()
  const unregister = useUnregisterWebhookMutation()
  const [shown, setShown] = useState<WebhookPreview | null>(null)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const target = { service: hook.service, instanceId: hook.instance_id }

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{hook.name}</p>
        <Badge variant={hook.connected ? "secondary" : "outline"} className="mt-1">
          {hook.connected ? t("realtime.webhooks.connected") : t("realtime.webhooks.notConnected")}
        </Badge>
      </div>
      {hook.connected ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={test.isPending}
            onClick={() =>
              test.mutate(target, {
                onSuccess: () => toast.success(t("realtime.webhooks.tested", { name: hook.name })),
                onError: (err) => failed(err, t("common.saveFailed")),
              })
            }
          >
            {test.isPending ? <Loader2 className="size-4 animate-spin" /> : <Zap className="size-4" />}
            {t("realtime.webhooks.test")}
          </Button>
          <Button
            type="button"
            variant={confirmRemove ? "destructive" : "ghost"}
            size="sm"
            disabled={unregister.isPending}
            onClick={() => {
              if (!confirmRemove) {
                setConfirmRemove(true)
                return
              }
              unregister.mutate(target, {
                onSuccess: () => toast.success(t("realtime.webhooks.disconnected", { name: hook.name })),
                onError: (err) => failed(err, t("common.saveFailed")),
                onSettled: () => setConfirmRemove(false),
              })
            }}
          >
            {unregister.isPending ? <Loader2 className="size-4 animate-spin" /> : <Unplug className="size-4" />}
            {confirmRemove ? t("realtime.webhooks.disconnectConfirm") : t("realtime.webhooks.disconnect")}
          </Button>
        </div>
      ) : (
        <Button
          type="button"
          size="sm"
          disabled={preview.isPending || !url.trim()}
          onClick={() =>
            preview.mutate(
              { ...target, url },
              { onSuccess: setShown, onError: (err) => failed(err, t("common.saveFailed")) },
            )
          }
        >
          {preview.isPending ? <Loader2 className="size-4 animate-spin" /> : <PlugZap className="size-4" />}
          {t("realtime.webhooks.connect")}
        </Button>
      )}
      <ConnectDialog hook={hook} preview={shown} url={url} onClose={() => setShown(null)} />
    </li>
  )
}

export function WebhooksCard({ settings }: { settings: RealtimeSettings }) {
  const { t } = useI18n()
  const [url, setUrl] = useState(() => defaultAnalysarrUrl(settings.analysarr_url, window.location.origin))

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("realtime.webhooks.title")}</CardTitle>
        <CardDescription>{t("realtime.webhooks.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="realtime-analysarr-url">{t("realtime.webhooks.address")}</Label>
          <Input
            id="realtime-analysarr-url"
            type="url"
            maxLength={500}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <p className="text-muted-foreground text-xs">{t("realtime.webhooks.addressHelp")}</p>
        </div>
        {settings.webhooks.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("realtime.webhooks.none")}</p>
        ) : (
          <ul className="divide-border divide-y">
            {settings.webhooks.map((hook) => (
              <WebhookRow key={`${hook.service}-${hook.instance_id}`} hook={hook} url={url} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
