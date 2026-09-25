import { useState } from "react"
import { Users } from "lucide-react"

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Skeleton } from "@/components/ui/skeleton"
import { usePreferences } from "@/hooks/use-app"
import { watchProgressLabel } from "@/components/media/watch-progress"
import { useMediaWatchQuery } from "@/hooks/use-media"
import { useI18n } from "@/i18n"
import { embyAvatarUrl } from "@/lib/api"
import { formatDate, formatDateTime, formatRelativeTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { MediaWatchStats, WatchUser } from "@/types/media"

// Avatar Emby relayé par le backend (jamais d'appel direct à Emby depuis le
// navigateur) ; initiale en repli s'il n'y en a pas ou si l'image échoue.
export function UserAvatar({
  user,
  className,
}: {
  user: { id: string; name: string; image_tag: string | null }
  className?: string
}) {
  const [failed, setFailed] = useState(false)
  const base = cn("size-7 shrink-0 rounded-full", className)
  if (user.image_tag && !failed) {
    return (
      <img
        src={embyAvatarUrl(user.id, user.image_tag)}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
        className={cn(base, "object-cover")}
      />
    )
  }
  return (
    <span
      aria-hidden
      className={cn(base, "bg-muted text-muted-foreground flex items-center justify-center text-xs font-medium uppercase")}
    >
      {user.name.trim().charAt(0) || "?"}
    </span>
  )
}

function WatchUserRow({ user, stats }: { user: WatchUser; stats: MediaWatchStats }) {
  const { t } = useI18n()
  const { absolute_dates } = usePreferences()
  const ratio = stats.total_episodes ? user.progress / stats.total_episodes : user.progress / 100
  const percent = Math.min(Math.max(ratio, 0), 1) * 100

  return (
    <li className="flex items-center gap-2.5 px-1 py-2">
      <UserAvatar user={user} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-sm font-medium">{user.name}</span>
          <span
            className={cn(
              "shrink-0 text-xs font-medium tabular-nums",
              user.played
                ? "text-emerald-600 dark:text-emerald-400"
                : user.in_progress
                  ? "text-amber-600 dark:text-amber-400"
                  : "text-muted-foreground",
            )}
          >
            {watchProgressLabel(user, stats, t)}
          </span>
        </div>
        <div className="bg-muted mt-1 h-1 overflow-hidden rounded-full">
          <div
            className={cn("h-full rounded-full", user.played ? "bg-emerald-500" : "bg-amber-500")}
            style={{ width: `${percent}%` }}
          />
        </div>
        {user.last_played_at && (
          <p className="text-muted-foreground mt-0.5 text-[11px]" title={formatDateTime(user.last_played_at) ?? undefined}>
            {absolute_dates
              ? t("watch.lastSeenOn", { date: formatDate(user.last_played_at) ?? "" })
              : t("watch.lastSeen", { time: formatRelativeTime(user.last_played_at) ?? "" })}
          </p>
        )}
      </div>
    </li>
  )
}

export function WatchQuota({ stats }: { stats: MediaWatchStats }) {
  const { t } = useI18n()
  const total = stats.users.length
  const summary = t("watch.summary", { count: stats.played_count, total })

  return (
    <Popover>
      <PopoverTrigger
        openOnHover
        delay={150}
        render={
          <button
            type="button"
            aria-label={summary}
            className="border-border hover:bg-muted inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm transition-colors"
          />
        }
      >
        <Users className="text-muted-foreground size-4" />
        <span className="font-medium tabular-nums">
          {stats.played_count}/{total}
        </span>
        {stats.in_progress_count > 0 && <span className="size-1.5 rounded-full bg-amber-500" aria-hidden />}
      </PopoverTrigger>
      <PopoverContent>
        <div className="px-1 pb-2">
          <p className="text-sm font-medium">{t("watch.title")}</p>
          <p className="text-muted-foreground text-xs">
            {summary}
            {stats.in_progress_count > 0 && ` · ${t("watch.inProgressCount", { count: stats.in_progress_count })}`}
          </p>
        </div>
        <ul className="divide-border divide-y border-t">
          {stats.users.map((user) => (
            <WatchUserRow key={user.id} user={user} stats={stats} />
          ))}
        </ul>
        {!stats.live && <p className="text-muted-foreground border-t px-1 pt-2 text-xs">{t("watch.stale")}</p>}
      </PopoverContent>
    </Popover>
  )
}

// Ligne de la fiche média : quota de visionnage, date d'ajout, dernière lecture.
export function WatchSummary({ mediaId }: { mediaId: number }) {
  const { t } = useI18n()
  const { absolute_dates } = usePreferences()
  const { data: stats, isLoading } = useMediaWatchQuery(mediaId)

  if (isLoading) return <Skeleton className="h-7 w-72" />
  if (!stats?.available) return null

  const added = stats.date_added
    ? absolute_dates
      ? t("watch.addedOn", { date: formatDate(stats.date_added) ?? "" })
      : t("watch.added", { time: formatRelativeTime(stats.date_added) ?? "" })
    : null
  const lastPlayed =
    stats.last_played_at && stats.last_played_by
      ? absolute_dates
        ? t("watch.lastPlayedOn", { date: formatDate(stats.last_played_at) ?? "", name: stats.last_played_by })
        : t("watch.lastPlayed", { time: formatRelativeTime(stats.last_played_at) ?? "", name: stats.last_played_by })
      : t("watch.neverPlayed")
  const details = [added, lastPlayed].filter(Boolean)

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      {stats.users.length > 0 ? (
        <WatchQuota stats={stats} />
      ) : (
        <span className="text-muted-foreground text-sm">{t("watch.noUsers")}</span>
      )}
      <span className="text-muted-foreground text-sm">{details.join(" · ")}</span>
    </div>
  )
}
