import { useState } from "react"
import { Loader2, RotateCcw, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  useDeleteTrashMutation,
  useEmptyTrashMutation,
  useRestoreTrashMutation,
  useSaveTrashSettingsMutation,
  useTrashQuery,
  useTrashSettingsQuery,
} from "@/hooks/use-trash"
import { useI18n } from "@/i18n"
import { formatBytes, formatDateTime } from "@/lib/format"

export function TrashSection() {
  const { t } = useI18n()
  const { data: settings, isLoading } = useTrashSettingsQuery()
  const { data: entries } = useTrashQuery()
  const saveMutation = useSaveTrashSettingsMutation()
  const restoreMutation = useRestoreTrashMutation()
  const deleteMutation = useDeleteTrashMutation()
  const emptyMutation = useEmptyTrashMutation()
  const [days, setDays] = useState<string | null>(null)

  if (isLoading) return <Skeleton className="h-64 w-full" />
  if (!settings) return <p className="text-destructive">{t("common.apiUnreachable")}</p>

  const value = days ?? String(settings.retention_days)
  const parsed = Number(value)
  const valid = Number.isInteger(parsed) && parsed >= settings.min_days && parsed <= settings.max_days
  const failed = (err: unknown) => toast.error(err instanceof Error ? err.message : t("common.saveFailed"))
  const total = (entries ?? []).reduce((sum, entry) => sum + (entry.available ? entry.size : 0), 0)

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("trash.title")}</CardTitle>
          <CardDescription>{t("trash.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="trash-enabled" className="font-normal">
              {t("trash.enabled")}
            </Label>
            <Switch
              id="trash-enabled"
              checked={settings.enabled}
              onCheckedChange={(checked) =>
                saveMutation.mutate(
                  { enabled: checked, retention_days: valid ? parsed : settings.retention_days },
                  { onError: failed },
                )
              }
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="trash-retention">{t("trash.retention")}</Label>
            <div className="flex items-center gap-2">
              <Input
                id="trash-retention"
                type="number"
                min={settings.min_days}
                max={settings.max_days}
                className="w-28"
                value={value}
                onChange={(e) => setDays(e.target.value)}
                onBlur={() => {
                  if (!valid || parsed === settings.retention_days) {
                    setDays(null)
                    return
                  }
                  saveMutation.mutate(
                    { enabled: settings.enabled, retention_days: parsed },
                    { onSuccess: () => setDays(null), onError: failed },
                  )
                }}
              />
              <span className="text-muted-foreground text-sm">{t("trash.days")}</span>
              {saveMutation.isPending && <Loader2 className="text-muted-foreground size-4 animate-spin" />}
            </div>
            <p className="text-muted-foreground text-xs">{t("trash.retentionHelp")}</p>
          </div>

          <p className="text-muted-foreground text-xs">{t("trash.scopeHelp")}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <CardTitle>{t("trash.contentTitle")}</CardTitle>
              <CardDescription>{t("trash.contentDescription", { size: formatBytes(total) })}</CardDescription>
            </div>
            {(entries?.length ?? 0) > 0 && (
              <Button
                type="button"
                variant="destructive"
                size="sm"
                disabled={emptyMutation.isPending}
                onClick={() =>
                  emptyMutation.mutate(undefined, {
                    onSuccess: () => toast.success(t("trash.emptied")),
                    onError: failed,
                  })
                }
              >
                {emptyMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
                {t("trash.empty")}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {entries && entries.length > 0 ? (
            <ul className="divide-border divide-y text-sm">
              {entries.map((entry) => (
                <li key={entry.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate" title={entry.original_path}>
                      {entry.original_path}
                    </p>
                    <p className="text-muted-foreground text-xs">
                      {entry.media_title ? `${entry.media_title} · ` : ""}
                      {formatDateTime(entry.deleted_at)} · {formatBytes(entry.size)}
                      {entry.available ? "" : ` · ${t("trash.missing")}`}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!entry.available || restoreMutation.isPending}
                      onClick={() =>
                        restoreMutation.mutate(entry.id, {
                          onSuccess: () => toast.success(t("trash.restored")),
                          onError: failed,
                        })
                      }
                    >
                      <RotateCcw className="size-4" />
                      {t("trash.restore")}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      title={t("trash.deleteNow")}
                      disabled={deleteMutation.isPending}
                      onClick={() => deleteMutation.mutate(entry.id, { onError: failed })}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground text-sm">{t("trash.empty0")}</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
