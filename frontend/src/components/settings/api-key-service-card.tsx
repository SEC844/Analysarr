import { Loader2 } from "lucide-react"

import { ConnectionTestAlert } from "@/components/settings/connection-test-alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useConnectionTest } from "@/hooks/use-connection-test"
import { MEDIA_SERVER_NAMES, useI18n, type MediaServer } from "@/i18n"

// Services configurés par URL + clé API : titre et exemple d'URL fixes, textes
// d'aide traduits (services.<service>.*). Le service "emby" désigne le serveur
// multimédia, Emby ou Jellyfin. Partagé entre Réglages et assistant.
const SERVICES = {
  emby: { title: "Emby", urlPlaceholder: "http://emby:8096" },
  jellyfin: { title: "Jellyfin", urlPlaceholder: "http://jellyfin:8096" },
  sonarr: { title: "Sonarr", urlPlaceholder: "http://sonarr:8989" },
  radarr: { title: "Radarr", urlPlaceholder: "http://radarr:7878" },
} as const

interface ApiKeyServiceCardProps {
  service: "emby" | "sonarr" | "radarr"
  url: string
  onUrlChange: (value: string) => void
  apiKey: string
  onApiKeyChange: (value: string) => void
  apiKeySet: boolean
  // Serveur multimédia uniquement.
  mediaServer?: MediaServer
  onMediaServerChange?: (value: MediaServer) => void
}

export function ApiKeyServiceCard({
  service,
  url,
  onUrlChange,
  apiKey,
  onApiKeyChange,
  apiKeySet,
  mediaServer = "emby",
  onMediaServerChange,
}: ApiKeyServiceCardProps) {
  const { t } = useI18n()
  const test = useConnectionTest(service)
  const variant = service === "emby" ? mediaServer : service
  const { title, urlPlaceholder } = SERVICES[variant]

  return (
    <Card>
      <CardHeader>
        <CardTitle>{service === "emby" ? t("settings.sections.mediaServer") : title}</CardTitle>
        <CardDescription>{t(`services.${variant}.description`)}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {service === "emby" && onMediaServerChange && (
          <div className="space-y-1.5">
            <Label htmlFor="media-server">{t("services.mediaServer")}</Label>
            <Select
              value={mediaServer}
              onValueChange={(v) => {
                onMediaServerChange(v as MediaServer)
                test.reset()
              }}
            >
              <SelectTrigger id="media-server" className="w-56">
                <SelectValue>{(v: string) => MEDIA_SERVER_NAMES[v as MediaServer] ?? v}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(MEDIA_SERVER_NAMES) as MediaServer[]).map((server) => (
                  <SelectItem key={server} value={server}>
                    {MEDIA_SERVER_NAMES[server]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <div className="space-y-1.5">
          <Label htmlFor={`${service}-url`}>{t("services.serverUrl")}</Label>
          <Input
            id={`${service}-url`}
            placeholder={urlPlaceholder}
            value={url}
            onChange={(e) => onUrlChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">{t(`services.${variant}.urlHelp`)}</p>
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
            {t(`services.${variant}.apiKeyHelp`)}
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
              ...(service === "emby" ? { media_server: mediaServer } : {}),
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
