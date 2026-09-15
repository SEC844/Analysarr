import { useState } from "react"
import { Link } from "react-router-dom"
import { Clapperboard, HardDrive, Tv, UserPlus, Users } from "lucide-react"

import { StatusBadgeList } from "@/components/media/status-badge"
import { usePreferences } from "@/hooks/use-app"
import { useI18n } from "@/i18n"
import { posterUrl } from "@/lib/api"
import { formatBytes } from "@/lib/format"
import type { MediaListItem } from "@/types/media"

export function MediaCard({ media }: { media: MediaListItem }) {
  const { t } = useI18n()
  const prefs = usePreferences()
  const [imgError, setImgError] = useState(false)
  const Icon = media.media_type === "movie" ? Clapperboard : Tv

  // Informations secondaires, chacune activable dans Réglages → Préférences.
  const showQuota = prefs.card_show_watch && media.has_emby_item && media.watch_user_count > 0
  const showSize = prefs.card_show_total_size && media.total_size > 0
  const showRequester = prefs.card_show_requested_by && Boolean(media.requested_by)
  const showReclaimable = prefs.card_show_reclaimable && media.reclaimable_bytes > 0

  return (
    <Link
      to={`/media/${media.id}`}
      className="group border-border bg-card hover:border-foreground/20 flex flex-col overflow-hidden rounded-lg border transition-colors"
    >
      <div className="bg-muted relative aspect-2/3 w-full overflow-hidden">
        {media.has_poster && !imgError ? (
          <img
            src={posterUrl(media.id, media.poster_image_tag)}
            alt=""
            loading="lazy"
            onError={() => setImgError(true)}
            className="h-full w-full object-cover transition-transform group-hover:scale-105"
          />
        ) : (
          <div className="text-muted-foreground flex h-full w-full items-center justify-center">
            <Icon className="size-10" />
          </div>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-3">
        <div>
          <p className="line-clamp-2 text-sm font-medium leading-tight">{media.title}</p>
          {media.year && <p className="text-muted-foreground text-xs">{media.year}</p>}
        </div>
        <StatusBadgeList statuses={media.statuses} />
        {(showQuota || showSize || showRequester || showReclaimable) && (
          <div className="text-muted-foreground mt-auto flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs">
            {showQuota && (
              <span
                className="inline-flex items-center gap-1 tabular-nums"
                title={t("watch.summary", { count: media.watch_played_count, total: media.watch_user_count })}
              >
                <Users className="size-3" />
                {media.watch_played_count}/{media.watch_user_count}
              </span>
            )}
            {showSize && (
              <span className="inline-flex items-center gap-1 tabular-nums" title={t("preferences.cardTotalSize")}>
                <HardDrive className="size-3" />
                {formatBytes(media.total_size)}
              </span>
            )}
            {showRequester && (
              <span
                className="inline-flex min-w-0 items-center gap-1"
                title={t("seer.requestedBy", { name: media.requested_by ?? "" })}
              >
                <UserPlus className="size-3 shrink-0" />
                <span className="truncate">{media.requested_by}</span>
              </span>
            )}
            {showReclaimable && (
              <span>{t("library.reclaimable", { size: formatBytes(media.reclaimable_bytes) })}</span>
            )}
          </div>
        )}
      </div>
    </Link>
  )
}
