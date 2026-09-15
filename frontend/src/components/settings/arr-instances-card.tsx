import { Loader2, Plus, Trash2 } from "lucide-react"

import { ConnectionTestAlert } from "@/components/settings/connection-test-alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useConnectionTest } from "@/hooks/use-connection-test"
import { useI18n } from "@/i18n"
import type { ArrInstanceForm, ArrInstanceRead, ArrKind } from "@/types/settings"

// Noms de services : jamais traduits.
const SERVICE_NAMES: Record<ArrKind, string> = { sonarr: "Sonarr", radarr: "Radarr" }
const PLACEHOLDERS: Record<ArrKind, { name: string; url: string }> = {
  sonarr: { name: "Sonarr Anime", url: "http://sonarr-anime:8989" },
  radarr: { name: "Radarr 4K", url: "http://radarr-4k:7878" },
}
// Même limite que le backend (services/arr_instances.py).
const MAX_EXTRA_INSTANCES = 10

let newInstanceCount = 0

function newInstance(kind: ArrKind): ArrInstanceForm {
  newInstanceCount += 1
  return { key: `new-${newInstanceCount}`, id: null, kind, name: "", url: "", api_key: "" }
}

interface InstanceRowProps {
  instance: ArrInstanceForm
  apiKeySet: boolean
  onChange: (value: ArrInstanceForm) => void
  onRemove: () => void
}

function InstanceRow({ instance, apiKeySet, onChange, onRemove }: InstanceRowProps) {
  const { t } = useI18n()
  const test = useConnectionTest(instance.kind)
  const id = `arr-instance-${instance.key}`
  const removeLabel = t("arrInstances.remove", { name: instance.name || SERVICE_NAMES[instance.kind] })

  return (
    <div className="space-y-4 py-4 first:pt-0 last:pb-0">
      <div className="flex items-end gap-2">
        <div className="min-w-0 flex-1 space-y-1.5">
          <Label htmlFor={`${id}-name`}>{t("arrInstances.name")}</Label>
          <Input
            id={`${id}-name`}
            placeholder={PLACEHOLDERS[instance.kind].name}
            value={instance.name}
            maxLength={40}
            onChange={(e) => onChange({ ...instance, name: e.target.value })}
            autoComplete="off"
          />
        </div>
        <Button type="button" variant="ghost" size="icon" onClick={onRemove} title={removeLabel} aria-label={removeLabel}>
          <Trash2 className="size-4" />
        </Button>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor={`${id}-url`}>{t("services.serverUrl")}</Label>
        <Input
          id={`${id}-url`}
          placeholder={PLACEHOLDERS[instance.kind].url}
          value={instance.url}
          onChange={(e) => {
            onChange({ ...instance, url: e.target.value })
            test.reset()
          }}
          autoComplete="off"
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor={`${id}-key`}>{t("common.apiKey")}</Label>
        <Input
          id={`${id}-key`}
          type="password"
          placeholder={apiKeySet ? t("common.keepSecretPlaceholder") : t("common.apiKey")}
          value={instance.api_key}
          onChange={(e) => onChange({ ...instance, api_key: e.target.value })}
          autoComplete="off"
        />
        {apiKeySet && <p className="text-muted-foreground text-sm">{t("services.apiKeySetHint").trim()}</p>}
      </div>

      <Button
        type="button"
        variant="secondary"
        disabled={test.isPending || !instance.url || !instance.api_key}
        onClick={() => test.mutate({ url: instance.url, api_key: instance.api_key })}
      >
        {test.isPending && <Loader2 className="size-4 animate-spin" />}
        {t("common.testConnection")}
      </Button>

      <ConnectionTestAlert result={test.data} />
    </div>
  )
}

interface ArrInstancesCardProps {
  kind: ArrKind
  // Toutes les instances du formulaire (Sonarr et Radarr) : seules celles de
  // `kind` sont affichées, les autres sont conservées telles quelles.
  instances: ArrInstanceForm[]
  saved: ArrInstanceRead[]
  onChange: (value: ArrInstanceForm[]) => void
}

export function ArrInstancesCard({ kind, instances, saved, onChange }: ArrInstancesCardProps) {
  const { t } = useI18n()
  const own = instances.filter((i) => i.kind === kind)
  const savedWithKey = new Set(saved.filter((s) => s.api_key_set).map((s) => s.id))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {t("arrInstances.title")}
          <Badge variant="outline">{t("common.optional")}</Badge>
        </CardTitle>
        <CardDescription>{t("arrInstances.description", { service: SERVICE_NAMES[kind] })}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {own.length > 0 && (
          <div className="divide-border divide-y">
            {own.map((instance) => (
              <InstanceRow
                key={instance.key}
                instance={instance}
                apiKeySet={instance.id !== null && savedWithKey.has(instance.id)}
                onChange={(next) => onChange(instances.map((i) => (i.key === next.key ? next : i)))}
                onRemove={() => onChange(instances.filter((i) => i.key !== instance.key))}
              />
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={own.length >= MAX_EXTRA_INSTANCES}
            onClick={() => onChange([...instances, newInstance(kind)])}
          >
            <Plus className="size-4" />
            {t("arrInstances.add")}
          </Button>
          {own.length > 0 && <p className="text-muted-foreground text-sm">{t("arrInstances.hint")}</p>}
        </div>
      </CardContent>
    </Card>
  )
}
