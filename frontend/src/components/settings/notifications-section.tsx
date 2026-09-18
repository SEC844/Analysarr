import { useState } from "react"
import { ArrowLeft, ChevronRight, Loader2, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { ChannelLogoTile } from "@/components/settings/channel-logos"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  useCreateChannelMutation,
  useDeleteChannelMutation,
  useNotificationChannelsQuery,
  useTestChannelMutation,
  useUpdateChannelMutation,
} from "@/hooks/use-notifications"
import { useI18n, type MessageKey } from "@/i18n"
import { cn } from "@/lib/utils"
import { NOTIFICATION_EVENTS, type ChannelKind, type NotificationChannel, type NotificationEvent } from "@/types/notifications"

// Noms de services : jamais traduits. `tokenMode` : ce que le service exige
// réellement (voir routers/notifications.py).
const KINDS: Record<ChannelKind, { name: string; urlSecret: boolean; urlPlaceholder: string; tokenMode: "none" | "optional" | "required" }> = {
  discord: {
    name: "Discord",
    urlSecret: true,
    urlPlaceholder: "https://discord.com/api/webhooks/…",
    tokenMode: "none",
  },
  ntfy: {
    name: "ntfy",
    urlSecret: true,
    urlPlaceholder: "https://ntfy.sh/mon-sujet",
    tokenMode: "optional",
  },
  gotify: {
    name: "Gotify",
    urlSecret: false,
    urlPlaceholder: "http://gotify:80",
    tokenMode: "required",
  },
}

// Sélection par défaut d'un nouveau canal, identique au backend.
const DEFAULT_EVENTS: NotificationEvent[] = [
  "scan_failed",
  "orphan_detected",
  "duplicate_detected",
  "delete_selection",
  "cascade_delete",
  "hardlink_repair",
]

function EventPicker({ events, onChange }: { events: NotificationEvent[]; onChange: (value: NotificationEvent[]) => void }) {
  const { t } = useI18n()
  const toggle = (event: NotificationEvent, checked: boolean) =>
    onChange(checked ? [...events, event] : events.filter((e) => e !== event))

  return (
    <div className="space-y-1.5">
      <Label>{t("notifications.events")}</Label>
      <div className="grid gap-2 sm:grid-cols-2">
        {NOTIFICATION_EVENTS.map((event) => (
          <label key={event} className="flex items-start gap-2 text-sm">
            <Checkbox checked={events.includes(event)} onCheckedChange={(checked) => toggle(event, checked === true)} />
            <span>
              <span className="block">{t(`notifications.eventNames.${event}` as MessageKey)}</span>
              <span className="text-muted-foreground block text-xs">
                {t(`notifications.eventHelp.${event}` as MessageKey)}
              </span>
            </span>
          </label>
        ))}
      </div>
    </div>
  )
}

interface ChannelCardProps {
  kind: ChannelKind
  channel?: NotificationChannel
  onDone?: () => void
}

function ChannelCard({ kind, channel, onDone }: ChannelCardProps) {
  const { t } = useI18n()
  const spec = KINDS[kind]
  // Canal existant replié : la liste reste courte et lisible.
  const [open, setOpen] = useState(!channel)
  const [name, setName] = useState(channel?.name ?? spec.name)
  const [url, setUrl] = useState(channel?.url ?? "")
  const [token, setToken] = useState("")
  const [enabled, setEnabled] = useState(channel?.enabled ?? true)
  const [events, setEvents] = useState<NotificationEvent[]>(channel?.events ?? DEFAULT_EVENTS)

  const create = useCreateChannelMutation()
  const update = useUpdateChannelMutation()
  const remove = useDeleteChannelMutation()
  const test = useTestChannelMutation()
  const [confirmDelete, setConfirmDelete] = useState(false)

  const onError = (err: unknown) => toast.error(err instanceof Error ? err.message : t("common.saveFailed"))
  const saving = create.isPending || update.isPending
  const canSave = !!name.trim() && (!!url.trim() || !!channel?.url_set)

  function handleSave() {
    const payload = { kind, name: name.trim(), enabled, events, url: url.trim(), token }
    if (channel) {
      update.mutate(
        { id: channel.id, payload },
        {
          onSuccess: () => {
            setToken("")
            toast.success(t("notifications.saved"))
          },
          onError,
        },
      )
      return
    }
    create.mutate(payload, {
      onSuccess: () => {
        toast.success(t("notifications.saved"))
        onDone?.()
      },
      onError,
    })
  }

  function handleTest() {
    if (!channel) return
    test.mutate(channel.id, {
      onSuccess: (result) =>
        result.ok
          ? toast.success(t("notifications.testSuccess", { name: channel.name }))
          : toast.error(t("notifications.testFailed", { details: result.error ?? "" })),
      onError,
    })
  }

  function handleDelete() {
    if (!channel) return
    if (!confirmDelete) {
      setConfirmDelete(true)
      return
    }
    remove.mutate(channel.id, {
      onSuccess: () => toast.success(t("notifications.deleted")),
      onError,
      onSettled: () => setConfirmDelete(false),
    })
  }

  return (
    <Card>
      <CardHeader className={cn(!open && "pb-4")}>
        <div className="flex items-center justify-between gap-4">
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            className="flex min-w-0 flex-1 items-center gap-3 text-left"
          >
            <ChevronRight className={cn("text-muted-foreground size-4 shrink-0 transition-transform", open && "rotate-90")} />
            <ChannelLogoTile kind={kind} />
            <span className="min-w-0">
              <CardTitle className="truncate">{channel ? channel.name : t("notifications.newChannel")}</CardTitle>
              <CardDescription className="truncate">
                {channel ? t("notifications.eventCount", { count: events.length }) : spec.name}
              </CardDescription>
            </span>
          </button>
          <div className="flex shrink-0 items-center gap-2">
            {channel && !enabled && <Badge variant="outline">{t("notifications.disabled")}</Badge>}
            <Switch checked={enabled} onCheckedChange={setEnabled} aria-label={t("notifications.enabled")} />
          </div>
        </div>
      </CardHeader>
      {open && (
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor={`channel-name-${channel?.id ?? "new"}`}>{t("notifications.name")}</Label>
            <Input
              id={`channel-name-${channel?.id ?? "new"}`}
              value={name}
              maxLength={40}
              onChange={(e) => setName(e.target.value)}
              autoComplete="off"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor={`channel-url-${channel?.id ?? "new"}`}>{t(`notifications.urlLabel.${kind}` as MessageKey)}</Label>
            <Input
              id={`channel-url-${channel?.id ?? "new"}`}
              type={spec.urlSecret ? "password" : "text"}
              placeholder={channel?.url_set && spec.urlSecret ? t("common.keepSecretPlaceholder") : spec.urlPlaceholder}
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              autoComplete="off"
            />
            <p className="text-muted-foreground text-sm">{t(`notifications.urlHelp.${kind}` as MessageKey)}</p>
          </div>

          {spec.tokenMode !== "none" && (
            <div className="space-y-1.5">
              <Label htmlFor={`channel-token-${channel?.id ?? "new"}`}>
                {spec.tokenMode === "required" ? t("notifications.token") : t("notifications.tokenOptional")}
              </Label>
              <Input
                id={`channel-token-${channel?.id ?? "new"}`}
                type="password"
                placeholder={channel?.token_set ? t("common.keepSecretPlaceholder") : ""}
                value={token}
                onChange={(e) => setToken(e.target.value)}
                autoComplete="off"
              />
            </div>
          )}

          <EventPicker events={events} onChange={setEvents} />

          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" disabled={!canSave || saving} onClick={handleSave}>
              {saving && <Loader2 className="size-4 animate-spin" />}
              {t("common.save")}
            </Button>
            {channel && (
              <Button type="button" variant="secondary" disabled={test.isPending} onClick={handleTest}>
                {test.isPending && <Loader2 className="size-4 animate-spin" />}
                {t("notifications.test")}
              </Button>
            )}
            {channel ? (
              <Button
                type="button"
                variant={confirmDelete ? "destructive" : "ghost"}
                disabled={remove.isPending}
                onClick={handleDelete}
                onBlur={() => setConfirmDelete(false)}
              >
                <Trash2 className="size-4" />
                {confirmDelete ? t("notifications.confirmDelete") : t("notifications.delete")}
              </Button>
            ) : (
              <Button type="button" variant="ghost" onClick={onDone}>
                {t("common.cancel")}
              </Button>
            )}
          </div>
        </CardContent>
      )}
    </Card>
  )
}

export function NotificationsSection() {
  const { t } = useI18n()
  const { data: channels, isLoading } = useNotificationChannelsQuery()
  const [kind, setKind] = useState<ChannelKind | null>(null)
  const [adding, setAdding] = useState(false)

  if (isLoading) return <Skeleton className="h-64 w-full" />
  if (!channels) return <p className="text-destructive">{t("common.apiUnreachable")}</p>

  if (kind === null) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t("notifications.title")}</CardTitle>
          <CardDescription>{t("notifications.description")}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-3">
          {(Object.keys(KINDS) as ChannelKind[]).map((available) => {
            const count = channels.filter((channel) => channel.kind === available).length
            return (
              <button
                key={available}
                type="button"
                onClick={() => {
                  setKind(available)
                  setAdding(count === 0)
                }}
                className="border-border hover:border-foreground/20 hover:bg-muted/50 flex flex-col items-center gap-2 rounded-lg border p-4 text-center transition-colors"
              >
                <ChannelLogoTile kind={available} />
                <span className="text-sm font-medium">{KINDS[available].name}</span>
                <span className="text-muted-foreground text-xs">
                  {count === 0 ? t("notifications.notConfigured") : t("notifications.channelCount", { count })}
                </span>
              </button>
            )
          })}
        </CardContent>
      </Card>
    )
  }

  const own = channels.filter((channel) => channel.kind === kind)
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={() => setKind(null)}>
          <ArrowLeft className="size-4" />
          {t("notifications.allChannels")}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={() => setAdding(true)} disabled={adding}>
          <Plus className="size-4" />
          {t("notifications.add", { service: KINDS[kind].name })}
        </Button>
      </div>

      {own.map((channel) => (
        <ChannelCard key={channel.id} kind={kind} channel={channel} />
      ))}
      {adding && <ChannelCard kind={kind} onDone={() => setAdding(false)} />}
      {!adding && own.length === 0 && <p className="text-muted-foreground text-sm">{t("notifications.empty")}</p>}
    </div>
  )
}
