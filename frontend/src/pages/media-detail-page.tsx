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
import { StatusBadgeList } from "@/components/media/status-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Skeleton } from "@/components/ui/skeleton"
import { useCrossSeedSearchMutation, useMediaDetailQuery } from "@/hooks/use-media"
import { useSettingsQuery } from "@/hooks/use-settings"
import type { CrossSeedSearchScope } from "@/lib/api"
import { posterUrl } from "@/lib/api"
import { formatBytes, formatDate, formatRatio } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { MediaFileRead } from "@/types/media"

// Section repliable au niveau carte (fichiers Emby / torrents qBittorrent) —
// fermée par défaut pour ne pas noyer la fiche sous une série à 177 épisodes.
function CollapsibleCard({
  title,
  defaultOpen = false,
  children,
}: {
  title: ReactNode
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
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

function seasonLabel(episodeLabel: string | null): string | null {
  const match = episodeLabel?.match(/^S(\d+)/)
  return match ? `Saison ${parseInt(match[1], 10)}` : null
}

function groupBySeason(files: MediaFileRead[]): { season: string; season_number: number; files: MediaFileRead[] }[] {
  const groups = new Map<string, { season_number: number; files: MediaFileRead[] }>()
  for (const f of files) {
    const label = seasonLabel(f.episode_label)
    const key = label ?? "Autres fichiers"
    const number = label ? parseInt(label.replace(/\D/g, ""), 10) : Number.MAX_SAFE_INTEGER
    if (!groups.has(key)) groups.set(key, { season_number: number, files: [] })
    groups.get(key)!.files.push(f)
  }
  return [...groups.entries()]
    .map(([season, g]) => ({ season, season_number: g.season_number, files: g.files }))
    .sort((a, b) => a.season_number - b.season_number)
}

function FileRow({ f }: { f: MediaFileRead }) {
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
              Fichier actuel
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
        <p className="text-destructive">Média introuvable.</p>
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
            toast.success(`Recherche cross-seed déclenchée (${result.triggered}).`)
          }
          if (result.errors.length > 0) {
            // Un message par fichier/torrent noierait l'écran dès qu'une série entière
            // échoue de la même façon : on ne montre que le détail (après le premier " : "),
            // dédupliqué, avec le nombre total d'échecs.
            const details = [...new Set(result.errors.map((e) => e.split(" : ").slice(1).join(" : ") || e))]
            toast.error(`${result.errors.length} échec(s) — ${details[0]}${details.length > 1 ? ` (+${details.length - 1} autre(s) type(s) d'erreur)` : ""}`)
          }
        },
        onError: (err) => toast.error(err instanceof Error ? err.message : "Échec de la recherche cross-seed."),
      },
    )
  }

  return (
    <div className="mx-auto max-w-[1600px] px-4 py-8 sm:px-6">
      <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="mb-4">
        <ArrowLeft className="size-4" />
        Retour à la bibliothèque
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
              {media.year}
              {totalSize > 0 && <> · {formatBytes(totalSize)}</>}
            </p>
          </div>
          <StatusBadgeList statuses={media.statuses} />
          {media.missing_emby_episodes.length > 0 && (
            <p className="text-muted-foreground text-sm">
              Téléchargés mais absents d'Emby : {media.missing_emby_episodes.join(", ")}
            </p>
          )}
          {media.reclaimable_bytes > 0 && (
            <p className="text-sm">
              <span className="font-medium">{formatBytes(media.reclaimable_bytes)}</span> potentiellement
              récupérables
            </p>
          )}

          <div className="flex flex-wrap gap-2 pt-2">
            {settings?.cross_seed.enabled &&
              (media.torrents.length > 0 || media.files.length > 0) &&
              (media.media_type === "series" ? (
                <DropdownMenu>
                  <DropdownMenuTrigger render={<Button type="button" variant="secondary" disabled={crossSeed.isPending} />}>
                    {crossSeed.isPending ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
                    Chercher un cross-seed
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuItem onClick={() => handleCrossSeed("episode")}>
                      Par épisode
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleCrossSeed("season")}>Par saison</DropdownMenuItem>
                    <DropdownMenuItem onClick={() => handleCrossSeed("series")}>Série intégrale</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              ) : (
                <Button type="button" variant="secondary" disabled={crossSeed.isPending} onClick={() => handleCrossSeed("episode")}>
                  {crossSeed.isPending ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
                  Chercher un cross-seed
                </Button>
              ))}
            {media.torrents.some((t) => t.is_hardlinked === false && t.repairable) && (
              <HardlinkRepairDialog mediaId={media.id} />
            )}
            {(media.statuses.includes("doublon") || media.statuses.includes("orphelin_qbit")) && (
              <DeleteCascadeDialog mediaId={media.id} />
            )}
            <MediaDeleteSelectionDialog media={media} onMediaDeleted={() => navigate(-1)} />
          </div>
        </div>
      </div>

      <CollapsibleCard title={`Fichiers Emby (${media.files.length})`}>
        {media.files.length === 0 ? (
          <p className="text-muted-foreground text-sm">Aucun fichier trouvé dans Emby pour ce média.</p>
        ) : media.media_type === "series" ? (
          groupBySeason(media.files).map((group) => (
            <SeasonGroup key={group.season} title={`${group.season} (${group.files.length})`}>
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

      <CollapsibleCard title={`Torrents qBittorrent (${media.torrents.length})`}>
        {media.torrents.length === 0 ? (
          <p className="text-muted-foreground text-sm">Aucun torrent associé à ce média.</p>
        ) : (
          <ul className="divide-border divide-y text-sm">
            {media.torrents.map((t) => (
              <li key={t.id} className="space-y-2 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="flex items-center gap-1.5 break-all font-medium">
                      {t.is_cross_seed && (
                        <span title="Ajouté par cross-seed" className="shrink-0">
                          <Search className="text-muted-foreground size-3.5" aria-label="Issu de cross-seed" />
                        </span>
                      )}
                      {t.name}
                    </p>
                    <p className="text-muted-foreground break-all text-xs">{t.content_path ?? t.save_path}</p>
                  </div>
                  <span className="text-muted-foreground shrink-0">{formatBytes(t.size)}</span>
                </div>

                <div className="flex flex-wrap items-center gap-1.5">
                  {t.is_hardlinked === true && (
                    <Badge variant="outline" className="border-transparent bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                      <CheckCircle2 className="size-3" /> Protégé (hardlink)
                    </Badge>
                  )}
                  {t.is_hardlinked === false && t.repairable && (
                    <Badge
                      variant="outline"
                      className="border-transparent bg-amber-500/10 text-amber-600 dark:text-amber-400"
                      title="Même contenu que la bibliothèque, mais pas hardlinké — réparable"
                    >
                      <Link2 className="size-3" /> Non hardlink
                    </Badge>
                  )}
                  {t.is_hardlinked === false && !t.repairable && (
                    <Badge variant="outline" className="border-transparent bg-destructive/10 text-destructive">
                      <XCircle className="size-3" /> Orphelin
                    </Badge>
                  )}
                  {t.is_hardlinked === null && (
                    <Badge variant="outline">
                      <HardDriveDownload className="size-3" /> Non évalué
                    </Badge>
                  )}
                  {t.trackers.map((tr, i) => (
                    <Badge key={i} variant="secondary">
                      {tr.domain} · {tr.status}
                    </Badge>
                  ))}
                </div>

                {(t.seeders !== null || t.leechers !== null || t.ratio !== null || t.completed_on) && (
                  <p className="text-muted-foreground text-xs">
                    {t.seeders !== null && t.leechers !== null && (
                      <>
                        {t.seeders} seeder{t.seeders > 1 ? "s" : ""} · {t.leechers} leecher{t.leechers > 1 ? "s" : ""}
                      </>
                    )}
                    {t.ratio !== null && <> · Ratio {formatRatio(t.ratio)}</>}
                    {formatDate(t.completed_on) && <> · Seedé depuis le {formatDate(t.completed_on)}</>}
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
