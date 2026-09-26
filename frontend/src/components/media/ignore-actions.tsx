import { useState } from "react"
import { Eye, EyeOff, Loader2, MoreHorizontal } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useCreateIgnoreMutation, useDeleteIgnoreMutation } from "@/hooks/use-ignores"
import { useI18n } from "@/i18n"
import type { IgnoreCreate } from "@/types/ignores"
import { INFO_STATUSES, type MediaDetail, type MediaFileRead, type MediaStatus, type TorrentRead } from "@/types/media"

/** Confirmation d'un ignore, avec une note facultative : rien n'est ignoré
 * sans que l'utilisateur ait lu ce que ça change. */
function IgnoreDialog({
  request,
  title,
  description,
  onClose,
  onIgnored,
}: {
  request: IgnoreCreate | null
  title: string
  description: string
  onClose: () => void
  onIgnored?: () => void
}) {
  const { t } = useI18n()
  const [note, setNote] = useState("")
  const create = useCreateIgnoreMutation()

  const close = () => {
    setNote("")
    onClose()
  }
  const confirm = () => {
    if (!request) return
    create.mutate(
      { ...request, note: note.trim() || undefined },
      {
        onSuccess: () => {
          toast.success(t("ignore.done"))
          close()
          onIgnored?.()
        },
        onError: (err) => toast.error(err instanceof Error ? err.message : t("ignore.failed")),
      },
    )
  }

  return (
    <Dialog open={request !== null} onOpenChange={(open) => !open && close()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="min-w-0 space-y-1.5">
          <Label htmlFor="ignore-note">{t("ignore.note")}</Label>
          <Input
            id="ignore-note"
            value={note}
            maxLength={200}
            placeholder={t("ignore.notePlaceholder")}
            onChange={(event) => setNote(event.target.value)}
          />
        </div>
        <DialogFooter>
          <Button type="button" disabled={create.isPending} onClick={confirm}>
            {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <EyeOff className="size-4" />}
            {t("ignore.confirm")}
          </Button>
          <Button type="button" variant="outline" onClick={close}>
            {t("common.cancel")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Ne plus ignorer : geste réversible, sans confirmation. */
function useUnignore() {
  const { t } = useI18n()
  const remove = useDeleteIgnoreMutation()
  return (ruleId: number) =>
    remove.mutate(ruleId, {
      onSuccess: () => toast.success(t("ignore.undone")),
      onError: (err) => toast.error(err instanceof Error ? err.message : t("ignore.failed")),
    })
}

function OptionsTrigger() {
  const { t } = useI18n()
  return (
    <DropdownMenuTrigger
      render={<Button type="button" variant="ghost" size="icon-sm" aria-label={t("ignore.options")} />}
    >
      <MoreHorizontal className="size-4" />
    </DropdownMenuTrigger>
  )
}

const MENU_CLASS = "w-auto min-w-48 max-w-80"

export function TorrentOptions({ mediaId, torrent }: { mediaId: number; torrent: TorrentRead }) {
  const { t } = useI18n()
  const unignore = useUnignore()
  const [request, setRequest] = useState<IgnoreCreate | null>(null)
  if (!torrent.ignorable && torrent.ignore_rule_id === null) return null
  const ruleId = torrent.ignore_rule_id

  return (
    <>
      <DropdownMenu>
        <OptionsTrigger />
        <DropdownMenuContent align="end" className={MENU_CLASS}>
          {torrent.ignorable && (
            <DropdownMenuItem onClick={() => setRequest({ media_id: mediaId, kind: "torrent", torrent_id: torrent.id })}>
              <EyeOff className="size-4" /> {t("ignore.ignoreTorrent")}
            </DropdownMenuItem>
          )}
          {ruleId !== null && (
            <DropdownMenuItem onClick={() => unignore(ruleId)}>
              <Eye className="size-4" /> {t("ignore.unignore")}
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <IgnoreDialog
        request={request}
        title={t("ignore.ignoreTorrentTitle")}
        description={t("ignore.ignoreTorrentDescription")}
        onClose={() => setRequest(null)}
      />
    </>
  )
}

export function FileOptions({ mediaId, file }: { mediaId: number; file: MediaFileRead }) {
  const { t } = useI18n()
  const unignore = useUnignore()
  const [request, setRequest] = useState<IgnoreCreate | null>(null)
  if (!file.ignorable && file.ignore_rule_id === null) return null
  const ruleId = file.ignore_rule_id

  return (
    <>
      <DropdownMenu>
        <OptionsTrigger />
        <DropdownMenuContent align="end" className={MENU_CLASS}>
          {file.ignorable && (
            <DropdownMenuItem onClick={() => setRequest({ media_id: mediaId, kind: "file", file_id: file.id })}>
              <EyeOff className="size-4" /> {t("ignore.keepFile")}
            </DropdownMenuItem>
          )}
          {ruleId !== null && (
            <DropdownMenuItem onClick={() => unignore(ruleId)}>
              <Eye className="size-4" /> {t("ignore.unkeepFile")}
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <IgnoreDialog
        request={request}
        title={t("ignore.keepFileTitle")}
        description={t("ignore.keepFileDescription")}
        onClose={() => setRequest(null)}
      />
    </>
  )
}

/** Alertes du média : les masquer une à une ou toutes, et réafficher celles
 * qui le sont. */
export function MediaAlertOptions({ media }: { media: MediaDetail }) {
  const { t } = useI18n()
  const unignore = useUnignore()
  const [statuses, setStatuses] = useState<MediaStatus[] | null>(null)
  const alerts = media.statuses.filter((status) => !INFO_STATUSES.includes(status))
  if (alerts.length === 0 && media.muted_rules.length === 0) return null

  const names = (list: MediaStatus[]) =>
    list.map((status) => t("ignore.quoted", { name: t(`status.${status}`) })).join(", ")

  return (
    <>
      <DropdownMenu>
        <OptionsTrigger />
        <DropdownMenuContent align="end" className={MENU_CLASS}>
          {alerts.map((status) => (
            <DropdownMenuItem key={status} onClick={() => setStatuses([status])}>
              <EyeOff className="size-4" /> {t("ignore.muteAlert", { status: t(`status.${status}`) })}
            </DropdownMenuItem>
          ))}
          {alerts.length > 1 && (
            <DropdownMenuItem onClick={() => setStatuses(alerts)}>
              <EyeOff className="size-4" /> {t("ignore.muteAll")}
            </DropdownMenuItem>
          )}
          {alerts.length > 0 && media.muted_rules.length > 0 && <DropdownMenuSeparator />}
          {media.muted_rules.map((rule) => (
            <DropdownMenuItem key={rule.rule_id} onClick={() => unignore(rule.rule_id)}>
              <Eye className="size-4" /> {t("ignore.unmute", { status: t(`status.${rule.status}`) })}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <IgnoreDialog
        request={statuses && { media_id: media.id, kind: "status", statuses }}
        title={t("ignore.muteTitle", { count: statuses?.length ?? 1 })}
        description={t("ignore.muteDescription", { alerts: names(statuses ?? []) })}
        onClose={() => setStatuses(null)}
      />
    </>
  )
}

/** Masquer une seule alerte, sans menu : bouton du dialogue « Lier à
 * Radarr/Sonarr » quand aucune fiche ne correspond au média. */
export function MuteStatusButton({
  mediaId,
  status,
  onDone,
}: {
  mediaId: number
  status: MediaStatus
  onDone: () => void
}) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button type="button" variant="secondary" onClick={() => setOpen(true)}>
        <EyeOff className="size-4" />
        {t("ignore.muteUntracked", { status: t(`status.${status}`) })}
      </Button>
      <IgnoreDialog
        request={open ? { media_id: mediaId, kind: "status", statuses: [status] } : null}
        title={t("ignore.muteTitle", { count: 1 })}
        description={t("ignore.muteDescription", {
          alerts: t("ignore.quoted", { name: t(`status.${status}`) }),
        })}
        onClose={() => setOpen(false)}
        onIgnored={onDone}
      />
    </>
  )
}
