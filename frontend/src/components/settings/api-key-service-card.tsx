import { Loader2 } from "lucide-react"

import { ConnectionTestAlert } from "@/components/settings/connection-test-alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useConnectionTest } from "@/hooks/use-connection-test"
import type { ServiceName } from "@/types/settings"

interface ApiKeyServiceCardProps {
  service: ServiceName
  title: string
  description: string
  url: string
  onUrlChange: (value: string) => void
  urlPlaceholder: string
  urlHelp: string
  apiKey: string
  onApiKeyChange: (value: string) => void
  apiKeyHelp: string
  apiKeySet: boolean
}

export function ApiKeyServiceCard({
  service,
  title,
  description,
  url,
  onUrlChange,
  urlPlaceholder,
  urlHelp,
  apiKey,
  onApiKeyChange,
  apiKeyHelp,
  apiKeySet,
}: ApiKeyServiceCardProps) {
  const test = useConnectionTest(service)

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor={`${service}-url`}>URL du serveur</Label>
          <Input
            id={`${service}-url`}
            placeholder={urlPlaceholder}
            value={url}
            onChange={(e) => onUrlChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">{urlHelp}</p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor={`${service}-key`}>Clé API</Label>
          <Input
            id={`${service}-key`}
            type="password"
            placeholder={apiKeySet ? "•••••••••••• (laisser vide pour conserver)" : "Clé API"}
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">
            {apiKeyHelp}
            {apiKeySet && " Une clé est déjà enregistrée ; ressaisissez-la ici pour la tester ou la changer."}
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
          Tester la connexion
        </Button>

        <ConnectionTestAlert result={test.data} />
      </CardContent>
    </Card>
  )
}
