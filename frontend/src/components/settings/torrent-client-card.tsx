import { Loader2 } from "lucide-react"

import { ConnectionTestAlert } from "@/components/settings/connection-test-alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useConnectionTest } from "@/hooks/use-connection-test"
import { useI18n } from "@/i18n"
import type { TorrentClientKind } from "@/types/settings"

// Noms de clients : jamais traduits. `username`/`password` : identifiants
// réellement exigés par chaque client (voir clients/torrent.py côté backend).
const CLIENTS: Record<TorrentClientKind, { name: string; url: string; username: boolean; password: boolean }> = {
  qbittorrent: { name: "qBittorrent", url: "http://qbittorrent:8080", username: true, password: true },
  deluge: { name: "Deluge", url: "http://deluge:8112", username: false, password: true },
  transmission: { name: "Transmission", url: "http://transmission:9091", username: false, password: false },
}

interface TorrentClientCardProps {
  client: TorrentClientKind
  onClientChange: (value: TorrentClientKind) => void
  url: string
  onUrlChange: (value: string) => void
  username: string
  onUsernameChange: (value: string) => void
  password: string
  onPasswordChange: (value: string) => void
  passwordSet: boolean
}

export function TorrentClientCard({
  client,
  onClientChange,
  url,
  onUrlChange,
  username,
  onUsernameChange,
  password,
  onPasswordChange,
  passwordSet,
}: TorrentClientCardProps) {
  const { t } = useI18n()
  const test = useConnectionTest("qbittorrent")
  const spec = CLIENTS[client]
  // Deluge n'a qu'un mot de passe d'interface web ; Transmission peut n'avoir
  // aucune authentification.
  const canTest = !!url && (!spec.username || !!username) && (!spec.password || !!password)

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("torrentClient.title")}</CardTitle>
        <CardDescription>{t("torrentClient.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="torrent-client">{t("torrentClient.kind")}</Label>
          <Select
            value={client}
            onValueChange={(v) => {
              onClientChange(v as TorrentClientKind)
              test.reset()
            }}
          >
            <SelectTrigger id="torrent-client" className="w-56">
              <SelectValue>{(v: string) => CLIENTS[v as TorrentClientKind]?.name ?? v}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(CLIENTS) as TorrentClientKind[]).map((kind) => (
                <SelectItem key={kind} value={kind}>
                  {CLIENTS[kind].name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="torrent-url">{t("torrentClient.webUrl")}</Label>
          <Input
            id="torrent-url"
            placeholder={spec.url}
            value={url}
            onChange={(e) => onUrlChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">{t("torrentClient.webUrlHelp")}</p>
        </div>

        {client !== "deluge" && (
          <div className="space-y-1.5">
            <Label htmlFor="torrent-username">{t("torrentClient.username")}</Label>
            <Input
              id="torrent-username"
              placeholder="admin"
              value={username}
              onChange={(e) => onUsernameChange(e.target.value)}
              autoComplete="off"
            />
          </div>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="torrent-password">{t("common.password")}</Label>
          <Input
            id="torrent-password"
            type="password"
            placeholder={passwordSet ? t("common.keepSecretPlaceholder") : t("common.password")}
            value={password}
            onChange={(e) => onPasswordChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">
            {t(`torrentClient.help.${client}`)}
            {passwordSet && t("torrentClient.passwordSetHint")}
          </p>
        </div>

        <Button
          type="button"
          variant="secondary"
          disabled={test.isPending || !canTest}
          onClick={() => test.mutate({ url, username, password, torrent_client: client })}
        >
          {test.isPending && <Loader2 className="size-4 animate-spin" />}
          {t("common.testConnection")}
        </Button>

        <ConnectionTestAlert result={test.data} />
      </CardContent>
    </Card>
  )
}
