import { useState } from "react"
import { Link } from "react-router-dom"
import { Clapperboard, Tv } from "lucide-react"

import { StatusBadgeList } from "@/components/media/status-badge"
import { posterUrl } from "@/lib/api"
import { formatBytes } from "@/lib/format"
import type { MediaListItem } from "@/types/media"

export function MediaCard({ media }: { media: MediaListItem }) {
  const [imgError, setImgError] = useState(false)
  const Icon = media.media_type === "movie" ? Clapperboard : Tv

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
        {media.reclaimable_bytes > 0 && (
          <p className="text-muted-foreground mt-auto text-xs">{formatBytes(media.reclaimable_bytes)} récupérables</p>
        )}
      </div>
    </Link>
  )
}
