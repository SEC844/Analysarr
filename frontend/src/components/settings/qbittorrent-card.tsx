import { Loader2 } from "lucide-react"

import { ConnectionTestAlert } from "@/components/settings/connection-test-alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useConnectionTest } from "@/hooks/use-connection-test"

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
  const test = useConnectionTest("qbittorrent")

  return (
    <Card>
      <CardHeader>
        <CardTitle>qBittorrent</CardTitle>
        <CardDescription>Client de téléchargement qui gère les torrents.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="qbit-url">URL de l'interface web</Label>
          <Input
            id="qbit-url"
            placeholder="http://qbittorrent:8080"
            value={url}
            onChange={(e) => onUrlChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">
            L'adresse de l'interface web qBittorrent (WebUI), accessible depuis le conteneur Analysarr.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="qbit-username">Identifiant</Label>
          <Input
            id="qbit-username"
            placeholder="admin"
            value={username}
            onChange={(e) => onUsernameChange(e.target.value)}
            autoComplete="off"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="qbit-password">Mot de passe</Label>
          <Input
            id="qbit-password"
            type="password"
            placeholder={passwordSet ? "•••••••••••• (laisser vide pour conserver)" : "Mot de passe"}
            value={password}
            onChange={(e) => onPasswordChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">
            Identifiants définis dans qBittorrent → Outils → Options → WebUI.
            {passwordSet && " Un mot de passe est déjà enregistré ; ressaisissez-le ici pour le tester ou le changer."}
          </p>
        </div>

        <Button
          type="button"
          variant="secondary"
          disabled={test.isPending || !url || !username || !password}
          onClick={() => test.mutate({ url, username, password })}
        >
          {test.isPending && <Loader2 className="size-4 animate-spin" />}
          Tester la connexion
        </Button>

        <ConnectionTestAlert result={test.data} />
      </CardContent>
    </Card>
  )
}
