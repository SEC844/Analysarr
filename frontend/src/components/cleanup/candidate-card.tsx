import { memo, useState } from "react"
import { Link } from "react-router-dom"
import { Clapperboard, HardDrive, Play, ShieldCheck, Tv } from "lucide-react"

import { ScoreBreakdown } from "@/components/cleanup/score-breakdown"
import { CleanupOptions } from "@/components/media/ignore-actions"
import { Checkbox } from "@/components/ui/checkbox"
import { useRecentlyUpdated } from "@/hooks/use-recently-updated"
import { useI18n } from "@/i18n"
import { posterUrl } from "@/lib/api"
import { mainReasonText, protectionText } from "@/lib/cleanup"
import { formatBytes } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { CleanupCandidate } from "@/types/cleanup"

function Poster({ candidate }: { candidate: CleanupCandidate }) {
  const [failed, setFailed] = useState(false)
  const Icon = candidate.media_type === "series" ? Tv : Clapperboard
  return candidate.has_poster && !failed ? (
    <img
      src={posterUrl(candidate.media_id, candidate.poster_image_tag)}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className="h-full w-full object-cover transition-transform group-hover:scale-105"
    />
  ) : (
    <span className="text-muted-foreground flex h-full w-full items-center justify-center">
      <Icon className="size-10" />
    </span>
  )
}

/** Carte d'un candidat, au format des cartes de la bibliothèque : case à
 * cocher et score posés sur la jaquette, raison et espace libérable dessous.
 * Mémoïsée : cocher une carte ne redessine pas toute la grille. */
export const CandidateCard = memo(function CandidateCard({
  candidate,
  selected,
  onToggle,
}: {
  candidate: CleanupCandidate
  selected: boolean
  onToggle: (candidate: CleanupCandidate) => void
}) {
  const { t } = useI18n()
  const recentlyUpdated = useRecentlyUpdated(candidate.media_id)
  const isProtected = candidate.protections.length > 0
  const detailUrl = `/media/${candidate.media_id}`
  const subtitle = [candidate.year, candidate.arr_instance_name].filter(Boolean).join(" · ")

  return (
    <article
      className={cn(
        "group bg-card relative flex flex-col overflow-hidden rounded-lg border transition-colors",
        selected ? "border-destructive ring-destructive/30 ring-2" : "border-border hover:border-foreground/20",
        recentlyUpdated && "live-updated",
      )}
    >
      {/* Lien doublé par le titre : retiré du clavier et des lecteurs d'écran. */}
      <Link
        to={detailUrl}
        tabIndex={-1}
        aria-hidden="true"
        className={cn("bg-muted relative block aspect-2/3 w-full overflow-hidden", isProtected && "opacity-60")}
      >
        <Poster candidate={candidate} />
      </Link>

      <div className="absolute top-2 left-2">
        {isProtected ? (
          <span
            className="bg-background/90 flex items-center gap-1 rounded-md px-1.5 py-1 text-xs font-medium text-sky-700 shadow-sm dark:text-sky-300"
            title={t("cleanup.protectedBadge")}
          >
            <ShieldCheck className="size-3.5" />
            <span className="sr-only">{t("cleanup.protectedBadge")}</span>
          </span>
        ) : (
          <span className="bg-background/90 flex rounded-md p-1.5 shadow-sm">
            <Checkbox
              checked={selected}
              onCheckedChange={() => onToggle(candidate)}
              aria-label={t("cleanup.select", { title: candidate.title })}
            />
          </span>
        )}
      </div>
      {!isProtected && (
        <div className="bg-background/90 absolute top-2 right-2 rounded-md shadow-sm">
          <ScoreBreakdown candidate={candidate} />
        </div>
      )}

      <div className="flex flex-1 flex-col gap-1.5 p-3">
        <div className="min-w-0">
          <Link to={detailUrl} className="line-clamp-2 text-sm leading-tight font-medium hover:underline">
            {candidate.title}
          </Link>
          {subtitle && <p className="text-muted-foreground truncate text-xs">{subtitle}</p>}
        </div>

        {isProtected ? (
          <ul className="space-y-0.5 text-xs text-sky-700 dark:text-sky-300">
            {candidate.protections.map((protection) => (
              <li key={protection.kind} className="line-clamp-2">
                {protectionText(t, protection)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground line-clamp-2 text-xs">{mainReasonText(t, candidate)}</p>
        )}
        {candidate.malus_in_progress && (
          <p className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-300">
            <Play className="size-3 shrink-0" />
            <span className="line-clamp-2">{t("cleanup.inProgressMalus")}</span>
          </p>
        )}

        <div className="mt-auto flex items-center justify-between gap-2 pt-1">
          <span
            className="text-muted-foreground inline-flex min-w-0 items-center gap-1 text-xs tabular-nums"
            title={t("cleanup.reclaimable", { size: formatBytes(candidate.reclaimable_bytes) })}
          >
            <HardDrive className="size-3 shrink-0" />
            <span className="truncate">{formatBytes(candidate.reclaimable_bytes)}</span>
          </span>
          {!isProtected && <CleanupOptions mediaId={candidate.media_id} />}
        </div>
      </div>
    </article>
  )
})
