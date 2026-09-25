import { Loader2 } from "lucide-react"

import { ConnectionTestAlert } from "@/components/settings/connection-test-alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { useConnectionTest } from "@/hooks/use-connection-test"
import { REQUEST_MANAGER_NAMES, useI18n, type RequestManager } from "@/i18n"

// Adresse par défaut de chaque gestionnaire, en exemple.
const URL_PLACEHOLDERS: Record<RequestManager, string> = { seer: "http://seerr:5055", ombi: "http://ombi:3579" }

interface SeerCardProps {
  enabled: boolean
  onEnabledChange: (value: boolean) => void
  kind: RequestManager
  onKindChange: (value: RequestManager) => void
  url: string
  onUrlChange: (value: string) => void
  apiKey: string
  onApiKeyChange: (value: string) => void
  apiKeySet: boolean
}

export function SeerCard({
  enabled,
  onEnabledChange,
  kind,
  onKindChange,
  url,
  onUrlChange,
  apiKey,
  onApiKeyChange,
  apiKeySet,
}: SeerCardProps) {
  const { t } = useI18n()
  const test = useConnectionTest("seer")
  const name = REQUEST_MANAGER_NAMES[kind]

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-2">
              {t("settings.sections.requestManager")}
              <Badge variant="outline">{t("common.optional")}</Badge>
            </CardTitle>
            <CardDescription>{t("seer.description")}</CardDescription>
          </div>
          <Switch id="seer-enabled" checked={enabled} onCheckedChange={onEnabledChange} />
        </div>
      </CardHeader>
      {enabled && (
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="seer-kind">{t("seer.kind")}</Label>
            <Select
              value={kind}
              onValueChange={(v) => {
                onKindChange(v as RequestManager)
                test.reset()
              }}
            >
              <SelectTrigger id="seer-kind" className="w-56">
                <SelectValue>{(v: string) => REQUEST_MANAGER_NAMES[v as RequestManager] ?? v}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(REQUEST_MANAGER_NAMES) as RequestManager[]).map((manager) => (
                  <SelectItem key={manager} value={manager}>
                    {REQUEST_MANAGER_NAMES[manager]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="seer-url">{t("seer.url", { requests: name })}</Label>
            <Input
              id="seer-url"
              placeholder={URL_PLACEHOLDERS[kind]}
              value={url}
              onChange={(e) => onUrlChange(e.target.value)}
              autoComplete="off"
            />
            <p className="text-muted-foreground text-sm">{t("services.sonarr.urlHelp")}</p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="seer-key">{t("common.apiKey")}</Label>
            <Input
              id="seer-key"
              type="password"
              placeholder={apiKeySet ? t("common.keepSecretPlaceholder") : t("common.apiKey")}
              value={apiKey}
              onChange={(e) => onApiKeyChange(e.target.value)}
              autoComplete="off"
            />
            <p className="text-muted-foreground text-sm">
              {t(`seer.apiKeyHelp.${kind}`)}
              {apiKeySet && t("services.apiKeySetHint")}
            </p>
          </div>

          <Button
            type="button"
            variant="secondary"
            disabled={test.isPending || !url || !apiKey}
            onClick={() => test.mutate({ url, api_key: apiKey, request_manager: kind })}
          >
            {test.isPending && <Loader2 className="size-4 animate-spin" />}
            {t("common.testConnection")}
          </Button>

          <ConnectionTestAlert result={test.data} />
        </CardContent>
      )}
    </Card>
  )
}
