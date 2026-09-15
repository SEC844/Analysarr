import { Loader2 } from "lucide-react"

import { ConnectionTestAlert } from "@/components/settings/connection-test-alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useConnectionTest } from "@/hooks/use-connection-test"
import { useI18n } from "@/i18n"

// Services configurés par URL + clé API : titre et exemple d'URL fixes, textes
// d'aide traduits (services.<service>.*). Partagé entre Réglages et assistant.
const SERVICES = {
  emby: { title: "Emby", urlPlaceholder: "http://emby:8096" },
  sonarr: { title: "Sonarr", urlPlaceholder: "http://sonarr:8989" },
  radarr: { title: "Radarr", urlPlaceholder: "http://radarr:7878" },
} as const

interface ApiKeyServiceCardProps {
  service: keyof typeof SERVICES
  url: string
  onUrlChange: (value: string) => void
  apiKey: string
  onApiKeyChange: (value: string) => void
  apiKeySet: boolean
}

export function ApiKeyServiceCard({ service, url, onUrlChange, apiKey, onApiKeyChange, apiKeySet }: ApiKeyServiceCardProps) {
  const { t } = useI18n()
  const test = useConnectionTest(service)
  const { title, urlPlaceholder } = SERVICES[service]

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{t(`services.${service}.description`)}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor={`${service}-url`}>{t("services.serverUrl")}</Label>
          <Input
            id={`${service}-url`}
            placeholder={urlPlaceholder}
            value={url}
            onChange={(e) => onUrlChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">{t(`services.${service}.urlHelp`)}</p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor={`${service}-key`}>{t("common.apiKey")}</Label>
          <Input
            id={`${service}-key`}
            type="password"
            placeholder={apiKeySet ? t("common.keepSecretPlaceholder") : t("common.apiKey")}
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">
            {t(`services.${service}.apiKeyHelp`)}
            {apiKeySet && t("services.apiKeySetHint")}
          </p>
        </div>

        <Button
          type="button"
          variant="secondary"
          disabled={test.isPending || !url || !apiKey}
          onClick={() =>
            test.mutate({
              url,
              api_key: apiKey,
            })
          }
        >
          {test.isPending && <Loader2 className="size-4 animate-spin" />}
          {t("common.testConnection")}
        </Button>

        <ConnectionTestAlert result={test.data} />
      </CardContent>
    </Card>
  )
}
