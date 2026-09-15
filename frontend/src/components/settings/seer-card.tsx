import { Loader2 } from "lucide-react"

import { ConnectionTestAlert } from "@/components/settings/connection-test-alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { useConnectionTest } from "@/hooks/use-connection-test"
import { useI18n } from "@/i18n"

interface SeerCardProps {
  enabled: boolean
  onEnabledChange: (value: boolean) => void
  url: string
  onUrlChange: (value: string) => void
  apiKey: string
  onApiKeyChange: (value: string) => void
  apiKeySet: boolean
}

export function SeerCard({ enabled, onEnabledChange, url, onUrlChange, apiKey, onApiKeyChange, apiKeySet }: SeerCardProps) {
  const { t } = useI18n()
  const test = useConnectionTest("seer")

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-2">
              Seer
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
            <Label htmlFor="seer-url">{t("seer.url")}</Label>
            <Input
              id="seer-url"
              placeholder="http://seerr:5055"
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
              {t("seer.apiKeyHelp")}
              {apiKeySet && t("services.apiKeySetHint")}
            </p>
          </div>

          <Button
            type="button"
            variant="secondary"
            disabled={test.isPending || !url || !apiKey}
            onClick={() => test.mutate({ url, api_key: apiKey })}
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
