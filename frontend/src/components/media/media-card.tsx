import { useState } from "react"
import { Link } from "react-router-dom"
import { Clapperboard, Tv, Users } from "lucide-react"

import { StatusBadgeList } from "@/components/media/status-badge"
import { useI18n } from "@/i18n"
import { posterUrl } from "@/lib/api"
import { formatBytes } from "@/lib/format"
import type { MediaListItem } from "@/types/media"

export function MediaCard({ media }: { media: MediaListItem }) {
  const { t } = useI18n()
  const [imgError, setImgError] = useState(false)
  const Icon = media.media_type === "movie" ? Clapperboard : Tv
  const showQuota = media.has_emby_item && media.watch_user_count > 0

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
        {(showQuota || media.reclaimable_bytes > 0) && (
          <div className="text-muted-foreground mt-auto flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-xs">
            {showQuota && (
              <span
                className="inline-flex items-center gap-1 tabular-nums"
                title={t("watch.summary", { count: media.watch_played_count, total: media.watch_user_count })}
              >
                <Users className="size-3" />
                {media.watch_played_count}/{media.watch_user_count}
              </span>
            )}
            {media.reclaimable_bytes > 0 && (
              <span>{t("library.reclaimable", { size: formatBytes(media.reclaimable_bytes) })}</span>
            )}
          </div>
        )}
      </div>
    </Link>
  )
}
