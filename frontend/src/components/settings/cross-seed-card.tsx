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
  const test = useConnectionTest("cross_seed")

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-2">
              cross-seed
              <Badge variant="outline">Optionnel</Badge>
            </CardTitle>
            <CardDescription>
              Permet de lancer une recherche cross-seed ciblée depuis une fiche média. N'est jamais requis pour
              utiliser Analysarr.
            </CardDescription>
          </div>
          <Switch id="cross-seed-enabled" checked={enabled} onCheckedChange={onEnabledChange} />
        </div>
      </CardHeader>
      {enabled && (
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="cross-seed-url">URL du daemon cross-seed</Label>
            <Input
              id="cross-seed-url"
              placeholder="http://cross-seed:2468"
              value={url}
              onChange={(e) => onUrlChange(e.target.value)}
              autoComplete="off"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="cross-seed-key">Clé API</Label>
            <Input
              id="cross-seed-key"
              type="password"
              placeholder={apiKeySet ? "•••••••••••• (laisser vide pour conserver)" : "Clé API"}
              value={apiKey}
              onChange={(e) => onApiKeyChange(e.target.value)}
              autoComplete="off"
            />
            <p className="text-muted-foreground text-sm">
              Trouvable dans la configuration du daemon cross-seed (champ <code>apiKey</code>).
            </p>
          </div>

          <Button
            type="button"
            variant="secondary"
            disabled={test.isPending || !url}
            onClick={() => test.mutate({ url, api_key: apiKey || undefined })}
          >
            {test.isPending && <Loader2 className="size-4 animate-spin" />}
            Tester la connexion
          </Button>

          <ConnectionTestAlert result={test.data} />

          <div className="space-y-1.5 border-t pt-4">
            <Label htmlFor="cross-seed-library-path">Bibliothèque vue depuis cross-seed</Label>
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
            <p className="text-muted-foreground text-sm">
              Optionnel — à remplir seulement si le conteneur cross-seed monte le même dossier de bibliothèque à un
              chemin différent de celui d'Analysarr (onglet Chemins). Sans ça, une recherche cross-seed lancée sur
              un média jamais seedé peut échouer avec « accessible path must be provided » — cross-seed ne
              retrouve pas le fichier sur son propre système de fichiers.
            </p>
          </div>
        </CardContent>
      )}
    </Card>
  )
}
