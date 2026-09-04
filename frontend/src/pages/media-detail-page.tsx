import { useNavigate, useParams } from "react-router-dom"
import { ArrowLeft, CheckCircle2, Clapperboard, HardDriveDownload, Link2, Loader2, Search, Tv, XCircle } from "lucide-react"
import { toast } from "sonner"

import { DeleteCascadeDialog } from "@/components/media/delete-cascade-dialog"
import { HardlinkRepairDialog } from "@/components/media/hardlink-repair-dialog"
import { StatusBadgeList } from "@/components/media/status-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useCrossSeedSearchMutation, useMediaDetailQuery } from "@/hooks/use-media"
import { useSettingsQuery } from "@/hooks/use-settings"
import { posterUrl } from "@/lib/api"
import { formatBytes, formatDate, formatRatio } from "@/lib/format"

export function MediaDetailPage() {
  const { id } = useParams<{ id: string }>()
  const mediaId = Number(id)
  const navigate = useNavigate()

  const { data: media, isLoading, isError } = useMediaDetailQuery(mediaId)
  const { data: settings } = useSettingsQuery()
  const crossSeed = useCrossSeedSearchMutation()

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="mt-4 h-64 w-full" />
      </div>
    )
  }

  if (isError || !media) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
        <p className="text-destructive">Média introuvable.</p>
      </div>
    )
  }

  const TypeIcon = media.media_type === "movie" ? Clapperboard : Tv

  // Poids réellement occupé sur le disque : les fichiers actuels (pas les
  // doublons ni les torrents orphelins, déjà comptés dans "récupérables").
  const currentFiles = media.files.filter((f) => f.is_current)
  const totalSize = (currentFiles.length > 0 ? currentFiles : media.files).reduce((sum, f) => sum + (f.size ?? 0), 0)

  const handleCrossSeed = () => {
    crossSeed.mutate(mediaId, {
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
    })
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="mb-4">
        <ArrowLeft className="size-4" />
        Retour à la bibliothèque
      </Button>

      <div className="flex flex-col gap-6 sm:flex-row">
        <div className="bg-muted flex aspect-2/3 w-40 shrink-0 items-center justify-center overflow-hidden rounded-lg">
          {media.has_poster ? (
            <img src={posterUrl(media.id)} alt="" className="h-full w-full object-cover" />
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
          {media.reclaimable_bytes > 0 && (
            <p className="text-sm">
              <span className="font-medium">{formatBytes(media.reclaimable_bytes)}</span> potentiellement
              récupérables
            </p>
          )}

          <div className="flex flex-wrap gap-2 pt-2">
            {settings?.cross_seed.enabled && (media.torrents.length > 0 || media.files.length > 0) && (
              <Button type="button" variant="secondary" disabled={crossSeed.isPending} onClick={handleCrossSeed}>
                {crossSeed.isPending ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
                Chercher un cross-seed
              </Button>
            )}
            {media.torrents.some((t) => t.is_hardlinked === false && t.matched_by_name) && (
              <HardlinkRepairDialog mediaId={media.id} />
            )}
            <DeleteCascadeDialog mediaId={media.id} />
          </div>
        </div>
      </div>

      <Card className="mt-8">
        <CardHeader>
          <CardTitle>Fichiers Emby ({media.files.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {media.files.length === 0 ? (
            <p className="text-muted-foreground text-sm">Aucun fichier trouvé dans Emby pour ce média.</p>
          ) : (
            <ul className="divide-border divide-y text-sm">
              {media.files.map((f) => (
                <li key={f.id} className="flex items-start justify-between gap-3 py-2">
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
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Torrents qBittorrent ({media.torrents.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {media.torrents.length === 0 ? (
            <p className="text-muted-foreground text-sm">Aucun torrent associé à ce média.</p>
          ) : (
            <ul className="divide-border divide-y text-sm">
              {media.torrents.map((t) => (
                <li key={t.id} className="space-y-2 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="break-all font-medium">{t.name}</p>
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
                    {t.is_hardlinked === false && t.matched_by_name && (
                      <Badge
                        variant="outline"
                        className="border-transparent bg-amber-500/10 text-amber-600 dark:text-amber-400"
                        title="Même contenu que la bibliothèque, mais pas hardlinké — réparable"
                      >
                        <Link2 className="size-3" /> Non hardlink
                      </Badge>
                    )}
                    {t.is_hardlinked === false && !t.matched_by_name && (
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
        </CardContent>
      </Card>
    </div>
  )
}
