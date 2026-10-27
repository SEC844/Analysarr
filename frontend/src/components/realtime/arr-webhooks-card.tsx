import { Link } from "react-router-dom"
import { CheckCircle2, Eye, Loader2, MinusCircle, RefreshCw, Send, TriangleAlert, Zap } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  useRetryWebhookMutation,
  useTestWebhookMutation,
  useWebhooksEnabledMutation,
  useWebhooksQuery,
} from "@/hooks/use-realtime"
import { useI18n } from "@/i18n"
import type { ArrService, WebhookRead } from "@/types/realtime"

const SERVICE_NAMES: Record<ArrService, string> = { sonarr: "Sonarr", radarr: "Radarr" }

function failed(err: unknown, fallback: string) {
  toast.error(err instanceof Error ? err.message : fallback)
}

function StateIcon({ state }: { state: WebhookRead["state"] }) {
  if (state === "connected") return <CheckCircle2 className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
  if (state === "pending" || state === "removing")
    return <Loader2 className="text-muted-foreground size-4 shrink-0 animate-spin" />
  if (state === "disabled") return <MinusCircle className="text-muted-foreground size-4 shrink-0" />
  return <TriangleAlert className="size-4 shrink-0 text-amber-600 dark:text-amber-400" />
}

/** Suivi en lecture seule (historique, file d'attente) : affiché quand c'est
 * lui qui assure le temps réel, c'est-à-dire sans webhook branché. */
function PollingLine({ hook }: { hook: WebhookRead }) {
  const { t } = useI18n()
  if (!hook.polling || hook.state === "connected") return null
  if (hook.polling === "error") {
    return (
      <p className="text-destructive text-xs break-words">
        {t("realtime.webhooks.polling.error", { error: hook.polling_error ?? "" })}
      </p>
    )
  }
  return (
    <p className="text-muted-foreground flex items-center gap-1 text-xs">
      <Eye className="size-3.5 shrink-0" />
      {t(`realtime.webhooks.polling.${hook.polling}`)}
    </p>
  )
}

function WebhookRow({ hook }: { hook: WebhookRead }) {
  const { t } = useI18n()
  const test = useTestWebhookMutation()
  const retry = useRetryWebhookMutation()
  const target = { service: hook.service, instanceId: hook.instance_id }

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2.5">
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="flex items-center gap-1.5 text-sm font-medium">
          <StateIcon state={hook.state} />
          <span className="truncate">{hook.name}</span>
          <span className="text-muted-foreground font-normal">· {t(`realtime.webhooks.states.${hook.state}`)}</span>
        </p>
        {hook.state === "error" && hook.error && <p className="text-destructive text-xs break-words">{hook.error}</p>}
        {hook.state === "no_address" && (
          <p className="text-muted-foreground text-xs">
            {t("realtime.webhooks.addressMissing")}{" "}
            <Link to="/settings?section=application" className="underline">
              {t("realtime.webhooks.openAddress")}
            </Link>
          </p>
        )}
        <PollingLine hook={hook} />
      </div>
      {hook.state === "connected" && (
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
          {test.isPending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
          {t("realtime.webhooks.test")}
        </Button>
      )}
      {hook.state === "error" && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={retry.isPending}
          onClick={() => retry.mutate(target, { onError: (err) => failed(err, t("common.saveFailed")) })}
        >
          <RefreshCw className={retry.isPending ? "size-4 animate-spin" : "size-4"} />
          {t("realtime.webhooks.retry")}
        </Button>
      )}
    </li>
  )
}

/** Temps réel d'un service Sonarr/Radarr : le webhook de chaque instance,
 * créé automatiquement par Analysarr tant que l'utilisateur l'autorise, et le
 * suivi en lecture seule qui prend le relais sans webhook. */
export function ArrWebhooksCard({ service }: { service: ArrService }) {
  const { t } = useI18n()
  const { data } = useWebhooksQuery()
  const setEnabled = useWebhooksEnabledMutation()
  const hooks = data?.webhooks.filter((hook) => hook.service === service) ?? []
  const name = SERVICE_NAMES[service]
  const switchId = `${service}-webhooks-enabled`

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Zap className="text-muted-foreground size-4" />
          {t("realtime.webhooks.title")}
        </CardTitle>
        <CardDescription>{t("realtime.webhooks.description", { service: name })}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {data && !data.realtime_enabled ? (
          <p className="text-muted-foreground text-sm">
            {t("realtime.webhooks.realtimeOff", { service: name })}{" "}
            <Link to="/settings?section=schedule" className="underline">
              {t("realtime.webhooks.openSchedule")}
            </Link>
          </p>
        ) : (
          <>
            {data && (
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0 space-y-1">
                  <Label htmlFor={switchId}>{t("realtime.webhooks.enabled", { service: name })}</Label>
                  <p className="text-muted-foreground text-sm">
                    {t(data.enabled ? "realtime.webhooks.enabledHelp" : "realtime.webhooks.disabledHelp", {
                      service: name,
                    })}
                  </p>
                </div>
                <Switch
                  id={switchId}
                  checked={data.enabled}
                  disabled={setEnabled.isPending}
                  onCheckedChange={(checked) =>
                    setEnabled.mutate(checked, { onError: (err) => failed(err, t("common.saveFailed")) })
                  }
                />
              </div>
            )}
            {hooks.length === 0 ? (
              <p className="text-muted-foreground text-sm">{t("realtime.webhooks.none")}</p>
            ) : (
              <ul className="divide-border divide-y border-t">
                {hooks.map((hook) => (
                  <WebhookRow key={`${hook.service}-${hook.instance_id}`} hook={hook} />
                ))}
              </ul>
            )}
            {data?.enabled && data.analysarr_url && (
              <p className="text-muted-foreground text-xs break-words">
                {t("realtime.webhooks.address", { url: data.analysarr_url })}{" "}
                <Link to="/settings?section=application" className="underline">
                  {t("realtime.webhooks.openAddress")}
                </Link>
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
