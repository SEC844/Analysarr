import { useState, type ReactNode } from "react"
import { useNavigate, useParams } from "react-router-dom"
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  Clapperboard,
  HardDriveDownload,
  Link2,
  Loader2,
  Search,
  Tv,
  XCircle,
} from "lucide-react"
import { toast } from "sonner"

import { DeleteCascadeDialog } from "@/components/media/delete-cascade-dialog"
import { HardlinkRepairDialog } from "@/components/media/hardlink-repair-dialog"
import { MediaDeleteSelectionDialog } from "@/components/media/media-delete-selection-dialog"
import { ImportIssues } from "@/components/media/import-issues"
import { MediaRequests } from "@/components/media/media-requests"
import { LinkToArrDialog } from "@/components/media/link-to-arr-dialog"
import { RescanMediaButton } from "@/components/media/rescan-media-button"
import { StatusBadgeList } from "@/components/media/status-badge"
import { WatchSummary } from "@/components/media/watch-stats"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Skeleton } from "@/components/ui/skeleton"
import { usePreferences } from "@/hooks/use-app"
import { useCrossSeedSearchMutation, useMediaDetailQuery } from "@/hooks/use-media"
import { useSettingsQuery } from "@/hooks/use-settings"
import { useI18n } from "@/i18n"
import type { CrossSeedSearchScope } from "@/lib/api"
import { posterUrl } from "@/lib/api"
import { formatBytes, formatDate, formatRatio } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { MediaFileRead } from "@/types/media"

// Section repliable au niveau carte (fichiers Emby / torrents qBittorrent) —
// fermée par défaut pour ne pas noyer la fiche sous une série à 177 épisodes.
function CollapsibleCard({ title, children }: { title: ReactNode; children: ReactNode }) {
  // Fermée par défaut, sauf préférence contraire (Réglages → Préférences).
  const { media_sections_expanded } = usePreferences()
  const [open, setOpen] = useState(media_sections_expanded)
  return (
    <Card className="mt-6 first:mt-8">
      <button type="button" onClick={() => setOpen((v) => !v)} className="w-full text-left" aria-expanded={open}>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>{title}</CardTitle>
          <ChevronDown className={cn("text-muted-foreground size-4 shrink-0 transition-transform", open && "rotate-180")} />
        </CardHeader>
      </button>
      {open && <CardContent>{children}</CardContent>}
    </Card>
  )
}

// Sous-section par saison, à l'intérieur de la carte "Fichiers Emby" — plus
// discrète (pas de carte imbriquée), fermée par défaut elle aussi.
function SeasonGroup({ title, children }: { title: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-border border-b last:border-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 py-2 text-left text-sm font-medium"
        aria-expanded={open}
      >
        {title}
        <ChevronDown className={cn("text-muted-foreground size-4 shrink-0 transition-transform", open && "rotate-180")} />
      </button>
      {open && <ul className="divide-border divide-y pb-2 text-sm">{children}</ul>}
    </div>
  )
}

// Numéro de saison extrait de "S01E02" ; null pour un fichier sans épisode identifié.
function seasonNumber(episodeLabel: string | null): number | null {
  const season = episodeLabel?.match(/^S(\d+)/)?.[1]
  return season ? parseInt(season, 10) : null
}

function groupBySeason(files: MediaFileRead[]): { season: number | null; files: MediaFileRead[] }[] {
  const groups = new Map<number | null, MediaFileRead[]>()
  for (const f of files) {
    const season = seasonNumber(f.episode_label)
    if (!groups.has(season)) groups.set(season, [])
    groups.get(season)!.push(f)
  }
  return [...groups.entries()]
    .map(([season, groupFiles]) => ({ season, files: groupFiles }))
    .sort((a, b) => (a.season ?? Number.MAX_SAFE_INTEGER) - (b.season ?? Number.MAX_SAFE_INTEGER))
}

function FileRow({ f }: { f: MediaFileRead }) {
  const { t } = useI18n()
  return (
    <li className="flex items-start justify-between gap-3 py-2">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          {f.episode_label && (
            <Badge variant="outline" className="shrink-0">
              {f.episode_label}
            </Badge>
          )}
          {f.is_current && (
            <Badge variant="outline" className="shrink-0 border-transparent bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
              {t("media.currentFile")}
            </Badge>
          )}
        </div>
        <p className="mt-1 break-all">{f.path}</p>
      </div>
      <span className="text-muted-foreground shrink-0">{formatBytes(f.size)}</span>
    </li>
  )
}

export function MediaDetailPage() {
  const { t, rich } = useI18n()
  const { id } = useParams<{ id: string }>()
  const mediaId = Number(id)
  const navigate = useNavigate()

  const { data: media, isLoading, isError } = useMediaDetailQuery(mediaId)
  const { data: settings } = useSettingsQuery()
  const crossSeed = useCrossSeedSearchMutation()

  if (isLoading) {
    return (
      <div className="mx-auto max-w-[1600px] px-4 py-8 sm:px-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="mt-4 h-64 w-full" />
      </div>
    )
  }

  if (isError || !media) {
    return (
      <div className="mx-auto max-w-[1600px] px-4 py-8 sm:px-6">
        <p className="text-destructive">{t("media.notFound")}</p>
      </div>
    )
  }

  const TypeIcon = media.media_type === "movie" ? Clapperboard : Tv

  // Poids réellement occupé sur le disque : les fichiers actuels (pas les
  // doublons ni les torrents orphelins, déjà comptés dans "récupérables").
  const currentFiles = media.files.filter((f) => f.is_current)
  const totalSize = (currentFiles.length > 0 ? currentFiles : media.files).reduce((sum, f) => sum + (f.size ?? 0), 0)

  const handleCrossSeed = (scope: CrossSeedSearchScope = "episode") => {
    crossSeed.mutate(
      { id: mediaId, scope },
      {
        onSuccess: (result) => {
          if (result.triggered > 0) {
            toast.success(t("media.crossSeedTriggered", { count: result.triggered }))
          }
          if (result.errors.length > 0) {
            // Un message par fichier/torrent noierait l'écran dès qu'une série entière
            // échoue de la même façon : on ne montre que le détail (après le premier " : "),
            // dédupliqué, avec le nombre total d'échecs.
            const details = [...new Set(result.errors.map((e) => e.split(" : ").slice(1).join(" : ") || e))]
            toast.error(
              t("media.crossSeedErrors", { count: result.errors.length, detail: details[0] ?? "" }) +
                (details.length > 1 ? t("media.crossSeedMoreErrors", { count: details.length - 1 }) : ""),
            )
          }
        },
        onError: (err) => toast.error(err instanceof Error ? err.message : t("media.crossSeedFailed")),
      },
    )
  }

  return (
    <div className="mx-auto max-w-[1600px] px-4 py-8 sm:px-6">
      <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="mb-4">
        <ArrowLeft className="size-4" />
        {t("media.back")}
      </Button>

      <div className="flex flex-col gap-6 sm:flex-row">
        <div className="bg-muted flex aspect-2/3 w-40 shrink-0 items-center justify-center overflow-hidden rounded-lg">
          {media.has_poster ? (
            <img src={posterUrl(media.id, media.poster_image_tag)} alt="" className="h-full w-full object-cover" />
          ) : (
            <TypeIcon className="text-muted-foreground size-10" />
          )}
        </div>

        <div className="flex-1 space-y-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{media.title}</h1>
            <p className="text-muted-foreground text-sm">
              {[media.year, media.arr_instance_name].filter(Boolean).join(" · ")}
              {totalSize > 0 && <> · {formatBytes(totalSize)}</>}
            </p>
          </div>
          <StatusBadgeList statuses={media.statuses} />
          <WatchSummary mediaId={media.id} />
          <MediaRequests requests={media.requests} />
          <ImportIssues mediaId={media.id} issues={media.import_issues} />
          {media.missing_emby_episodes.length > 0 && (
            <p className="text-muted-foreground text-sm">
              {t("media.missingEmby", { list: media.missing_emby_episodes.join(", ") })}
            </p>
          )}
          {media.reclaimable_bytes > 0 && (
            <p className="text-sm">
              {rich("media.reclaimable", {
                size: <span className="font-medium">{formatBytes(media.reclaimable_bytes)}</span>,
              })}
            </p>
          )}

          <div className="flex flex-wrap gap-2 pt-2">
            <RescanMediaButton mediaId={media.id} onMediaDeleted={() => navigate(-1)} />
            {/* Média suivi par personne : le rattacher se fait dans
                Sonarr/Radarr, le bouton explique comment. */}
            {media.statuses.includes("manquant_arr") && <LinkToArrDialog media={media} />}
            {settings?.cross_seed.enabled &&
              (media.torrents.length > 0 || media.files.length > 0) &&
              (media.media_type === "series" ? (
                <DropdownMenu>
                  <DropdownMenuTrigger render={<Button type="button" variant="secondary" disabled={crossSeed.isPending} />}>
                    {crossSeed.isPending ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
                    {t("media.crossSeedSearch")}
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuItem onClick={() => handleCrossSeed("episode")}>{t("media.byEpisode")}</DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleCrossSeed("season")}>{t("media.bySeason")}</DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleCrossSeed("series")}>{t("media.wholeSeries")}</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              ) : (
                <Button type="button" variant="secondary" disabled={crossSeed.isPending} onClick={() => handleCrossSeed("episode")}>
                  {crossSeed.isPending ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
                  {t("media.crossSeedSearch")}
                </Button>
              ))}
            {media.torrents.some((tr) => tr.is_hardlinked === false && tr.repairable) && (
              <HardlinkRepairDialog mediaId={media.id} />
            )}
            {(media.statuses.includes("doublon") || media.statuses.includes("orphelin_qbit")) && (
              <DeleteCascadeDialog mediaId={media.id} />
            )}
            <MediaDeleteSelectionDialog media={media} onMediaDeleted={() => navigate(-1)} />
          </div>
        </div>
      </div>

      <CollapsibleCard title={t("media.embyFiles", { count: media.files.length })}>
        {media.files.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("media.noEmbyFiles")}</p>
        ) : media.media_type === "series" ? (
          groupBySeason(media.files).map((group) => (
            <SeasonGroup
              key={group.season ?? "other"}
              title={`${group.season !== null ? t("media.season", { number: group.season }) : t("media.otherFiles")} (${group.files.length})`}
            >
              {group.files.map((f) => (
                <FileRow key={f.id} f={f} />
              ))}
            </SeasonGroup>
          ))
        ) : (
          <ul className="divide-border divide-y text-sm">
            {media.files.map((f) => (
              <FileRow key={f.id} f={f} />
            ))}
          </ul>
        )}
      </CollapsibleCard>

      <CollapsibleCard title={t("media.torrents", { count: media.torrents.length })}>
        {media.torrents.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("media.noTorrents")}</p>
        ) : (
          <ul className="divide-border divide-y text-sm">
            {media.torrents.map((torrent) => (
              <li key={torrent.id} className="space-y-2 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="flex items-center gap-1.5 break-all font-medium">
                      {torrent.is_cross_seed && (
                        <span title={t("media.addedByCrossSeed")} className="shrink-0">
                          <Search className="text-muted-foreground size-3.5" aria-label={t("media.fromCrossSeed")} />
                        </span>
                      )}
                      {torrent.name}
                    </p>
                    <p className="text-muted-foreground break-all text-xs">{torrent.content_path ?? torrent.save_path}</p>
                  </div>
                  <span className="text-muted-foreground shrink-0">{formatBytes(torrent.size)}</span>
                </div>

                <div className="flex flex-wrap items-center gap-1.5">
                  {torrent.is_hardlinked === true && (
                    <Badge variant="outline" className="border-transparent bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                      <CheckCircle2 className="size-3" /> {t("media.protected")}
                    </Badge>
                  )}
                  {torrent.is_hardlinked === false && torrent.repairable && (
                    <Badge
                      variant="outline"
                      className="border-transparent bg-amber-500/10 text-amber-600 dark:text-amber-400"
                      title={t("media.notHardlinkedHint")}
                    >
                      <Link2 className="size-3" /> {t("media.notHardlinked")}
                    </Badge>
                  )}
                  {torrent.is_hardlinked === false && !torrent.repairable && (
                    <Badge variant="outline" className="border-transparent bg-destructive/10 text-destructive">
                      <XCircle className="size-3" /> {t("media.orphan")}
                    </Badge>
                  )}
                  {torrent.is_hardlinked === null && (
                    <Badge variant="outline">
                      <HardDriveDownload className="size-3" /> {t("media.notEvaluated")}
                    </Badge>
                  )}
                  {torrent.trackers.map((tr, i) => (
                    <Badge key={i} variant="secondary">
                      {tr.domain} · {tr.status}
                    </Badge>
                  ))}
                </div>

                {(torrent.seeders !== null || torrent.leechers !== null || torrent.ratio !== null || torrent.completed_on) && (
                  <p className="text-muted-foreground text-xs">
                    {torrent.seeders !== null && torrent.leechers !== null && (
                      <>
                        {t("media.seeders", { count: torrent.seeders })} · {t("media.leechers", { count: torrent.leechers })}
                      </>
                    )}
                    {torrent.ratio !== null && <> · {t("media.ratio", { ratio: formatRatio(torrent.ratio) ?? "" })}</>}
                    {formatDate(torrent.completed_on) && (
                      <> · {t("media.seedingSince", { date: formatDate(torrent.completed_on) ?? "" })}</>
                    )}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </CollapsibleCard>
    </div>
  )
}
