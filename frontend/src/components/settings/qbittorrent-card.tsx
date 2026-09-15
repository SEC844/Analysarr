import { Loader2 } from "lucide-react"

import { ConnectionTestAlert } from "@/components/settings/connection-test-alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useConnectionTest } from "@/hooks/use-connection-test"
import { useI18n } from "@/i18n"

interface QbittorrentCardProps {
  url: string
  onUrlChange: (value: string) => void
  username: string
  onUsernameChange: (value: string) => void
  password: string
  onPasswordChange: (value: string) => void
  passwordSet: boolean
}

export function QbittorrentCard({
  url,
  onUrlChange,
  username,
  onUsernameChange,
  password,
  onPasswordChange,
  passwordSet,
}: QbittorrentCardProps) {
  const { t } = useI18n()
  const test = useConnectionTest("qbittorrent")

  return (
    <Card>
      <CardHeader>
        <CardTitle>qBittorrent</CardTitle>
        <CardDescription>{t("qbittorrent.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="qbit-url">{t("qbittorrent.webUrl")}</Label>
          <Input
            id="qbit-url"
            placeholder="http://qbittorrent:8080"
            value={url}
            onChange={(e) => onUrlChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">{t("qbittorrent.webUrlHelp")}</p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="qbit-username">{t("qbittorrent.username")}</Label>
          <Input
            id="qbit-username"
            placeholder="admin"
            value={username}
            onChange={(e) => onUsernameChange(e.target.value)}
            autoComplete="off"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="qbit-password">{t("common.password")}</Label>
          <Input
            id="qbit-password"
            type="password"
            placeholder={passwordSet ? t("common.keepSecretPlaceholder") : t("common.password")}
            value={password}
            onChange={(e) => onPasswordChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">
            {t("qbittorrent.credentialsHelp")}
            {passwordSet && t("qbittorrent.passwordSetHint")}
          </p>
        </div>

        <Button
          type="button"
          variant="secondary"
          disabled={test.isPending || !url || !username || !password}
          onClick={() => test.mutate({ url, username, password })}
        >
          {test.isPending && <Loader2 className="size-4 animate-spin" />}
          {t("common.testConnection")}
        </Button>

        <ConnectionTestAlert result={test.data} />
      </CardContent>
    </Card>
  )
}
