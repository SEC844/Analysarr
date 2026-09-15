import { useState } from "react"
import { CheckCircle2, Copy, KeyRound, Loader2, ShieldOff } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useCreateWidgetKeyMutation, useRevokeWidgetKeyMutation, useWidgetKeyQuery } from "@/hooks/use-widget"
import { useI18n } from "@/i18n"
import { copyText } from "@/lib/clipboard"

function CopyButton({ text }: { text: string }) {
  const { t } = useI18n()

  async function handleCopy() {
    // HTTPS, ou HTTP sur le réseau local uniquement (voir lib/clipboard.ts).
    if (await copyText(text)) toast.success(t("twoFactor.copied"))
    else toast.error(t("twoFactor.copyFailed"))
  }

  return (
    <Button type="button" variant="outline" size="sm" onClick={handleCopy}>
      <Copy className="size-4" />
      {t("widget.copy")}
    </Button>
  )
}

export function WidgetSection() {
  const { t, rich } = useI18n()
  const { data, isLoading } = useWidgetKeyQuery()
  const create = useCreateWidgetKeyMutation()
  const revoke = useRevokeWidgetKeyMutation()
  // Clé affichée une seule fois, juste après sa génération (jamais relue).
  const [newKey, setNewKey] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<"regenerate" | "revoke" | null>(null)

  if (isLoading) return <Skeleton className="h-64 w-full" />
  if (!data) return <p className="text-destructive">{t("common.apiUnreachable")}</p>

  const enabled = data.enabled
  const origin = window.location.origin
  const endpoint = `${origin}/api/status`
  const onError = (err: unknown) => toast.error(err instanceof Error ? err.message : t("common.saveFailed"))

  function handleCreate() {
    if (enabled && confirming !== "regenerate") {
      setConfirming("regenerate")
      return
    }
    create.mutate(undefined, {
      onSuccess: (res) => {
        setNewKey(res.key)
        toast.success(t("widget.generated"))
      },
      onError,
      onSettled: () => setConfirming(null),
    })
  }

  function handleRevoke() {
    if (confirming !== "revoke") {
      setConfirming("revoke")
      return
    }
    revoke.mutate(undefined, {
      onSuccess: () => {
        setNewKey(null)
        toast.success(t("widget.revoked"))
      },
      onError,
      onSettled: () => setConfirming(null),
    })
  }

  const snippet = [
    "- Analysarr:",
    `    href: ${origin}`,
    "    widget:",
    "      type: customapi",
    `      url: ${endpoint}`,
    "      headers:",
    `        X-Api-Key: ${newKey ?? t("widget.keyPlaceholder")}`,
    "      mappings:",
    "        - field: { media: total }",
    `          label: ${t("widget.labels.media")}`,
    "        - field: { statuses: doublon }",
    `          label: ${t("widget.labels.duplicates")}`,
    "        - field: reclaimable_bytes",
    `          label: ${t("widget.labels.reclaimable")}`,
    "          format: bytes",
  ].join("\n")

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>{t("widget.title")}</CardTitle>
            <CardDescription>{t("widget.description")}</CardDescription>
          </div>
          {enabled ? (
            <Badge variant="outline" className="border-transparent bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="size-3" />
              {t("widget.enabled")}
            </Badge>
          ) : (
            <Badge variant="outline">{t("widget.disabled")}</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {newKey && (
          <div className="space-y-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
            <p className="font-medium">{t("widget.newKey")}</p>
            <code className="bg-background/60 block rounded-md border px-2 py-1.5 font-mono text-xs break-all select-all">
              {newKey}
            </code>
            <CopyButton text={newKey} />
          </div>
        )}

        <div className="space-y-1.5">
          <p className="text-sm font-medium">{t("widget.endpoint")}</p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="bg-muted/50 rounded-md border px-2 py-1.5 font-mono text-xs break-all">{endpoint}</code>
            <CopyButton text={endpoint} />
          </div>
          <p className="text-muted-foreground text-sm">
            {rich("widget.header", { header: <code className="font-mono text-xs">X-Api-Key</code> })}
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant={!enabled ? "default" : confirming === "regenerate" ? "destructive" : "secondary"}
            disabled={create.isPending}
            onClick={handleCreate}
            onBlur={() => setConfirming((c) => (c === "regenerate" ? null : c))}
          >
            {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" />}
            {!enabled
              ? t("widget.generate")
              : confirming === "regenerate"
                ? t("widget.confirmRegenerate")
                : t("widget.regenerate")}
          </Button>
          {enabled && (
            <Button
              type="button"
              variant={confirming === "revoke" ? "destructive" : "ghost"}
              disabled={revoke.isPending}
              onClick={handleRevoke}
              onBlur={() => setConfirming((c) => (c === "revoke" ? null : c))}
            >
              {revoke.isPending ? <Loader2 className="size-4 animate-spin" /> : <ShieldOff className="size-4" />}
              {confirming === "revoke" ? t("widget.confirmRevoke") : t("widget.revoke")}
            </Button>
          )}
        </div>
        <p className="text-muted-foreground text-sm">{t("widget.security")}</p>

        <div className="space-y-2 border-t pt-4">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-medium">{t("widget.example")}</p>
            <CopyButton text={snippet} />
          </div>
          <pre className="bg-muted/50 overflow-x-auto rounded-md border p-3 font-mono text-xs">{snippet}</pre>
        </div>
      </CardContent>
    </Card>
  )
}
