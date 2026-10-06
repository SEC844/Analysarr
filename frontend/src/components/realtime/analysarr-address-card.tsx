import { useState, type FormEvent } from "react"
import { Loader2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useSaveAddressMutation, useWebhooksQuery } from "@/hooks/use-realtime"
import { useI18n } from "@/i18n"

function AddressForm({ saved }: { saved: string }) {
  const { t } = useI18n()
  const save = useSaveAddressMutation()
  const [url, setUrl] = useState(saved)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    save.mutate(
      { url: url.trim() },
      {
        onSuccess: () => toast.success(t("realtime.address.saved")),
        onError: (err) => toast.error(err instanceof Error ? err.message : t("common.saveFailed")),
      },
    )
  }

  return (
    <form className="flex flex-wrap items-end gap-2" onSubmit={submit}>
      <div className="min-w-0 flex-1 space-y-1.5">
        <Label htmlFor="analysarr-address">{t("realtime.address.label")}</Label>
        <Input
          id="analysarr-address"
          type="url"
          placeholder="http://analysarr:1818"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          autoComplete="off"
        />
      </div>
      <Button type="submit" disabled={!url.trim() || url.trim() === saved || save.isPending}>
        {save.isPending && <Loader2 className="size-4 animate-spin" />}
        {t("common.save")}
      </Button>
    </form>
  )
}

/** Adresse d'Analysarr vue par Sonarr/Radarr (webhooks du temps réel) :
 * détectée depuis le navigateur à la première visite, corrigeable ici. */
export function AnalysarrAddressCard() {
  const { t } = useI18n()
  const { data } = useWebhooksQuery()

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("realtime.address.title")}</CardTitle>
        <CardDescription>{t("realtime.address.description")}</CardDescription>
      </CardHeader>
      <CardContent>{data && <AddressForm key={data.analysarr_url} saved={data.analysarr_url} />}</CardContent>
    </Card>
  )
}
