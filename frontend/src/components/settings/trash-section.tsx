import { useState } from "react"
import { ChevronRight, FileVideo, Loader2, Magnet, RotateCcw, Trash2 } from "lucide-react"
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
import { cn } from "@/lib/utils"
import type { TrashAction } from "@/types/trash"

function ActionRow({ action }: { action: TrashAction }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const restoreMutation = useRestoreTrashMutation()
  const deleteMutation = useDeleteTrashMutation()
  const [confirmDelete, setConfirmDelete] = useState(false)

  const torrents = action.items.filter((item) => item.kind === "torrent").length
  const files = action.items.length - torrents
  const extras = [
    files > 0 ? t("trash.fileCount", { count: files }) : null,
    torrents > 0 ? t("trash.torrentCount", { count: torrents }) : null,
    action.restores_arr ? t("trash.withArr") : null,
    action.restores_seer ? t("trash.withSeer") : null,
  ].filter(Boolean)

  return (
    <li className="py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          aria-expanded={open}
        >
          <ChevronRight className={cn("size-4 shrink-0 transition-transform", open && "rotate-90")} />
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium">{action.media_title}</span>
            <span className="text-muted-foreground block text-xs">
              {formatDateTime(action.created_at)} · {formatBytes(action.size)} · {extras.join(" · ")}
            </span>
          </span>
        </button>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!action.restorable || restoreMutation.isPending}
            title={action.restorable ? undefined : t("trash.incomplete")}
            onClick={() =>
              restoreMutation.mutate(action.id, {
                onSuccess: (result) => {
                  if (!result.complete) {
                    toast.error(result.steps.find((step) => !step.success)?.error ?? t("trash.restoreFailed"))
                    return
                  }
                  // Une analyse suit la restauration : le média revient sur
                  // l'accueil sans scan manuel.
                  toast.success(result.rescan_started ? t("trash.restoredWithScan") : t("trash.restored"))
                },
                onError: (err) => toast.error(err instanceof Error ? err.message : t("trash.restoreFailed")),
              })
            }
          >
            {restoreMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}
            {t("trash.restore")}
          </Button>
          {confirmDelete ? (
            <>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                disabled={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate(action.id, { onSuccess: () => setConfirmDelete(false) })}
              >
                {t("common.confirmDelete")}
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={() => setConfirmDelete(false)}>
                {t("common.cancel")}
              </Button>
            </>
          ) : (
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              title={t("trash.deleteNow")}
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 className="size-4" />
            </Button>
          )}
        </div>
      </div>

      {open && (
        <ul className="text-muted-foreground mt-2 space-y-1 pl-6 text-xs">
          {action.items.map((item, index) => (
            <li key={`${item.label}-${index}`} className="flex items-center gap-2">
              {item.kind === "torrent" ? (
                <Magnet className="size-3.5 shrink-0" />
              ) : (
                <FileVideo className="size-3.5 shrink-0" />
              )}
              <span className="min-w-0 flex-1 truncate" title={item.original_path ?? item.label}>
                {item.label}
              </span>
              <span className="tabular-nums">{formatBytes(item.size)}</span>
              {!item.available && <span className="text-destructive">{t("trash.missing")}</span>}
            </li>
          ))}
          {action.restores_arr && <li className="pl-5">{t("trash.withArrDetail")}</li>}
          {action.restores_seer && <li className="pl-5">{t("trash.withSeerDetail")}</li>}
        </ul>
      )}
    </li>
  )
}

export function TrashSection() {
  const { t } = useI18n()
  const { data: settings, isLoading } = useTrashSettingsQuery()
  const { data: actions } = useTrashQuery()
  const saveMutation = useSaveTrashSettingsMutation()
  const emptyMutation = useEmptyTrashMutation()
  const [days, setDays] = useState<string | null>(null)
  const [confirmEmpty, setConfirmEmpty] = useState(false)

  if (isLoading) return <Skeleton className="h-64 w-full" />
  if (!settings) return <p className="text-destructive">{t("common.apiUnreachable")}</p>

  const value = days ?? String(settings.retention_days)
  const parsed = Number(value)
  const valid = Number.isInteger(parsed) && parsed >= settings.min_days && parsed <= settings.max_days
  const failed = (err: unknown) => toast.error(err instanceof Error ? err.message : t("common.saveFailed"))
  const total = (actions ?? []).reduce((sum, action) => sum + action.size, 0)

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
          <p className="text-muted-foreground text-xs">{t("trash.ratioHelp")}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <CardTitle>{t("trash.contentTitle")}</CardTitle>
              <CardDescription>{t("trash.contentDescription", { size: formatBytes(total) })}</CardDescription>
            </div>
            {(actions?.length ?? 0) > 0 &&
              (confirmEmpty ? (
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    disabled={emptyMutation.isPending}
                    onClick={() =>
                      emptyMutation.mutate(undefined, {
                        onSuccess: () => {
                          setConfirmEmpty(false)
                          toast.success(t("trash.emptied"))
                        },
                        onError: failed,
                      })
                    }
                  >
                    {emptyMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
                    {t("common.confirmDelete")}
                  </Button>
                  <Button type="button" variant="ghost" size="sm" onClick={() => setConfirmEmpty(false)}>
                    {t("common.cancel")}
                  </Button>
                </div>
              ) : (
                <Button type="button" variant="destructive" size="sm" onClick={() => setConfirmEmpty(true)}>
                  <Trash2 className="size-4" />
                  {t("trash.empty")}
                </Button>
              ))}
          </div>
        </CardHeader>
        <CardContent>
          {actions && actions.length > 0 ? (
            <ul className="divide-border divide-y text-sm">
              {actions.map((action) => (
                <ActionRow key={action.id} action={action} />
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
