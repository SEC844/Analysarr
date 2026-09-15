import { Loader2 } from "lucide-react"

import { ConnectionTestAlert } from "@/components/settings/connection-test-alert"
import { PathBrowserButton } from "@/components/settings/path-browser-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { useConnectionTest } from "@/hooks/use-connection-test"
import { useI18n } from "@/i18n"

interface CrossSeedCardProps {
  enabled: boolean
  onEnabledChange: (value: boolean) => void
  url: string
  onUrlChange: (value: string) => void
  apiKey: string
  onApiKeyChange: (value: string) => void
  apiKeySet: boolean
  libraryPath: string
  onLibraryPathChange: (value: string) => void
}

export function CrossSeedCard({
  enabled,
  onEnabledChange,
  url,
  onUrlChange,
  apiKey,
  onApiKeyChange,
  apiKeySet,
  libraryPath,
  onLibraryPathChange,
}: CrossSeedCardProps) {
  const { t, rich } = useI18n()
  const test = useConnectionTest("cross_seed")

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-2">
              cross-seed
              <Badge variant="outline">{t("common.optional")}</Badge>
            </CardTitle>
            <CardDescription>{t("crossSeed.description")}</CardDescription>
          </div>
          <Switch id="cross-seed-enabled" checked={enabled} onCheckedChange={onEnabledChange} />
        </div>
      </CardHeader>
      {enabled && (
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="cross-seed-url">{t("crossSeed.daemonUrl")}</Label>
            <Input
              id="cross-seed-url"
              placeholder="http://cross-seed:2468"
              value={url}
              onChange={(e) => onUrlChange(e.target.value)}
              autoComplete="off"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="cross-seed-key">{t("common.apiKey")}</Label>
            <Input
              id="cross-seed-key"
              type="password"
              placeholder={apiKeySet ? t("common.keepSecretPlaceholder") : t("common.apiKey")}
              value={apiKey}
              onChange={(e) => onApiKeyChange(e.target.value)}
              autoComplete="off"
            />
            <p className="text-muted-foreground text-sm">{rich("crossSeed.apiKeyHelp", { field: <code>apiKey</code> })}</p>
          </div>

          <Button
            type="button"
            variant="secondary"
            disabled={test.isPending || !url}
            onClick={() => test.mutate({ url, api_key: apiKey || undefined })}
          >
            {test.isPending && <Loader2 className="size-4 animate-spin" />}
            {t("common.testConnection")}
          </Button>

          <ConnectionTestAlert result={test.data} />

          <div className="space-y-1.5 border-t pt-4">
            <Label htmlFor="cross-seed-library-path">{t("crossSeed.libraryPath")}</Label>
            <div className="flex gap-2">
              <Input
                id="cross-seed-library-path"
                placeholder="/data/media"
                value={libraryPath}
                onChange={(e) => onLibraryPathChange(e.target.value)}
                autoComplete="off"
              />
              <PathBrowserButton value={libraryPath} onSelect={onLibraryPathChange} />
            </div>
            <p className="text-muted-foreground text-sm">{t("crossSeed.libraryPathHelp")}</p>
          </div>
        </CardContent>
      )}
    </Card>
  )
}
