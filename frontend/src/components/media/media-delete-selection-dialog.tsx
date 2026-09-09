import { useMemo, useState } from "react"
import { CheckCircle2, Loader2, Trash2, XCircle } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { useDeleteSelectionExecuteMutation } from "@/hooks/use-media"
import { formatBytes } from "@/lib/format"
import type { MediaDeleteSelectionResult, MediaDetail, MediaFileRead } from "@/types/media"

function seasonLabel(episodeLabel: string | null): string {
  const match = episodeLabel?.match(/^S(\d+)/)
  return match ? `Saison ${parseInt(match[1], 10)}` : "Autres fichiers"
}

function groupBySeason(files: MediaFileRead[]): [string, MediaFileRead[]][] {
  const groups = new Map<string, MediaFileRead[]>()
  for (const f of files) {
    const key = seasonLabel(f.episode_label)
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(f)
  }
  return [...groups.entries()]
}

function toggleIds(ids: number[], checked: boolean, set: React.Dispatch<React.SetStateAction<Set<number>>>) {
  set((prev) => {
    const next = new Set(prev)
    for (const id of ids) {
      if (checked) next.add(id)
      else next.delete(id)
    }
    return next
  })
}

export function MediaDeleteSelectionDialog({ media }: { media: MediaDetail }) {
  const [open, setOpen] = useState(false)
  const [selectedTorrentIds, setSelectedTorrentIds] = useState<Set<number>>(new Set())
  const [selectedFileIds, setSelectedFileIds] = useState<Set<number>>(new Set())
  const [removeFromArr, setRemoveFromArr] = useState(false)
  const [result, setResult] = useState<MediaDeleteSelectionResult | null>(null)

  const executeMutation = useDeleteSelectionExecuteMutation()

  const filesBySeason = useMemo(
    () => (media.media_type === "series" ? groupBySeason(media.files) : null),
    [media.files, media.media_type],
  )

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (!next) {
      setSelectedTorrentIds(new Set())
      setSelectedFileIds(new Set())
      setRemoveFromArr(false)
      setResult(null)
    }
  }

  const totalSize =
    media.torrents.filter((t) => selectedTorrentIds.has(t.id)).reduce((sum, t) => sum + (t.size ?? 0), 0) +
    media.files.filter((f) => selectedFileIds.has(f.id)).reduce((sum, f) => sum + (f.size ?? 0), 0)

  const hasSelection = selectedTorrentIds.size > 0 || selectedFileIds.size > 0

  const handleConfirm = () => {
    executeMutation.mutate(
      {
        id: media.id,
        selection: {
          torrent_ids: [...selectedTorrentIds],
          media_file_ids: [...selectedFileIds],
          remove_from_arr: removeFromArr,
        },
      },
      {
        onSuccess: (data) => {
          setResult(data)
          const failedCount = data.steps.filter((s) => !s.success).length
          if (failedCount === 0) toast.success("Suppression effectuée.")
          else toast.error(`${failedCount} échec(s) sur ${data.steps.length}.`)
        },
        onError: (err) => toast.error(err instanceof Error ? err.message : "Échec de la suppression."),
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger render={<Button type="button" variant="outline" size="icon" title="Supprimer..." />}>
        <Trash2 className="size-4" />
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Supprimer</DialogTitle>
          <DialogDescription>
            Choisissez ce qu'il faut supprimer — torrents, fichiers de bibliothèque (par épisode, saison ou média
            entier) — et confirmez.
          </DialogDescription>
        </DialogHeader>

        {!result && (
          <div className="max-h-96 space-y-4 overflow-y-auto">
            {media.torrents.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-sm font-medium">Torrents</p>
                <ul className="divide-border divide-y text-sm">
                  {media.torrents.map((t) => (
                    <li key={t.id} className="flex items-center gap-2 py-1.5">
                      <Checkbox
                        checked={selectedTorrentIds.has(t.id)}
                        onCheckedChange={(checked) => toggleIds([t.id], checked, setSelectedTorrentIds)}
                      />
                      <span className="min-w-0 flex-1 truncate">{t.name}</span>
                      <span className="text-muted-foreground shrink-0 text-xs">{formatBytes(t.size)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {media.files.length > 0 && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium">Bibliothèque</p>
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    onClick={() =>
                      toggleIds(
                        media.files.map((f) => f.id),
                        !media.files.every((f) => selectedFileIds.has(f.id)),
                        setSelectedFileIds,
                      )
                    }
                  >
                    {media.media_type === "series" ? "Toute la série" : "Tout sélectionner"}
                  </Button>
                </div>
                {filesBySeason ? (
                  filesBySeason.map(([season, files]) => (
                    <div key={season} className="space-y-1">
                      <div className="flex items-center justify-between">
                        <p className="text-muted-foreground text-xs font-medium">{season}</p>
                        <Button
                          type="button"
                          variant="ghost"
                          size="xs"
                          onClick={() =>
                            toggleIds(
                              files.map((f) => f.id),
                              !files.every((f) => selectedFileIds.has(f.id)),
                              setSelectedFileIds,
                            )
                          }
                        >
                          Toute la saison
                        </Button>
                      </div>
                      <ul className="divide-border divide-y text-sm">
                        {files.map((f) => (
                          <li key={f.id} className="flex items-center gap-2 py-1.5">
                            <Checkbox
                              checked={selectedFileIds.has(f.id)}
                              onCheckedChange={(checked) => toggleIds([f.id], checked, setSelectedFileIds)}
                            />
                            <span className="min-w-0 flex-1 truncate">{f.episode_label ?? f.path}</span>
                            <span className="text-muted-foreground shrink-0 text-xs">{formatBytes(f.size)}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))
                ) : (
                  <ul className="divide-border divide-y text-sm">
                    {media.files.map((f) => (
                      <li key={f.id} className="flex items-center gap-2 py-1.5">
                        <Checkbox
                          checked={selectedFileIds.has(f.id)}
                          onCheckedChange={(checked) => toggleIds([f.id], checked, setSelectedFileIds)}
                        />
                        <span className="min-w-0 flex-1 truncate">{f.path}</span>
                        <span className="text-muted-foreground shrink-0 text-xs">{formatBytes(f.size)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {selectedFileIds.size > 0 && (
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={removeFromArr} onCheckedChange={setRemoveFromArr} />
                Supprimer aussi de {media.media_type === "movie" ? "Radarr" : "Sonarr"} (empêche un
                retéléchargement automatique)
              </label>
            )}

            {hasSelection && <p className="text-sm font-medium">Total : {formatBytes(totalSize)}</p>}
          </div>
        )}

        {result && (
          <ul className="max-h-96 space-y-1 overflow-y-auto text-sm">
            {result.steps.map((step, i) => (
              <li key={i} className="flex items-start gap-2 py-1">
                {step.success ? (
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-500" />
                ) : (
                  <XCircle className="text-destructive mt-0.5 size-4 shrink-0" />
                )}
                <div>
                  <p className="break-all">{step.label}</p>
                  {step.error && <p className="text-destructive text-xs">{step.error}</p>}
                </div>
              </li>
            ))}
          </ul>
        )}

        <DialogFooter>
          {!result && (
            <Button
              type="button"
              variant="destructive"
              disabled={!hasSelection || executeMutation.isPending}
              onClick={handleConfirm}
            >
              {executeMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Trash2 className="size-4" />
              )}
              Confirmer la suppression
            </Button>
          )}
          <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
            Fermer
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
