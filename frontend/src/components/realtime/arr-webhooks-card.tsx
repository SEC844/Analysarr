import { Link } from "react-router-dom"
import { CheckCircle2, Loader2, RefreshCw, Send, TriangleAlert, Zap } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useRetryWebhookMutation, useTestWebhookMutation, useWebhooksQuery } from "@/hooks/use-realtime"
import { useI18n } from "@/i18n"
import type { ArrService, WebhookRead } from "@/types/realtime"

function failed(err: unknown, fallback: string) {
  toast.error(err instanceof Error ? err.message : fallback)
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
          {hook.state === "connected" ? (
            <CheckCircle2 className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
          ) : hook.state === "pending" ? (
            <Loader2 className="text-muted-foreground size-4 shrink-0 animate-spin" />
          ) : (
            <TriangleAlert className="size-4 shrink-0 text-amber-600 dark:text-amber-400" />
          )}
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
 * créé automatiquement par Analysarr dès que l'instance est enregistrée. */
export function ArrWebhooksCard({ service }: { service: ArrService }) {
  const { t } = useI18n()
  const { data } = useWebhooksQuery()
  const hooks = data?.webhooks.filter((hook) => hook.service === service) ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Zap className="text-muted-foreground size-4" />
          {t("realtime.webhooks.title")}
        </CardTitle>
        <CardDescription>{t("realtime.webhooks.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {hooks.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("realtime.webhooks.none")}</p>
        ) : (
          <ul className="divide-border divide-y">
            {hooks.map((hook) => (
              <WebhookRow key={`${hook.service}-${hook.instance_id}`} hook={hook} />
            ))}
          </ul>
        )}
        {data?.analysarr_url && (
          <p className="text-muted-foreground pt-2 text-xs">
            {t("realtime.webhooks.address", { url: data.analysarr_url })}{" "}
            <Link to="/settings?section=application" className="underline">
              {t("realtime.webhooks.openAddress")}
            </Link>
          </p>
        )}
      </CardContent>
    </Card>
  )
}
