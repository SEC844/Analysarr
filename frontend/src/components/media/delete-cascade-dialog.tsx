import { useEffect, useState } from "react"
import { AlertTriangle, CheckCircle2, Loader2, Trash2, XCircle } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { useDeleteExecuteMutation, useDeletePreviewMutation } from "@/hooks/use-media"
import { formatBytes } from "@/lib/format"
import type { DeleteExecuteResult, DeletePreview } from "@/types/media"

const KIND_LABELS: Record<string, string> = {
  duplicate_file: "Fichier en doublon",
  orphan_torrent: "Torrent orphelin",
}

export function DeleteCascadeDialog({ mediaId }: { mediaId: number }) {
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<DeletePreview | null>(null)
  const [result, setResult] = useState<DeleteExecuteResult | null>(null)

  const previewMutation = useDeletePreviewMutation()
  const executeMutation = useDeleteExecuteMutation()

  useEffect(() => {
    if (open) {
      setResult(null)
      previewMutation.mutate(mediaId, { onSuccess: setPreview })
    } else {
      setPreview(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, mediaId])

  const handleConfirm = () => {
    executeMutation.mutate(mediaId, { onSuccess: setResult })
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="destructive" />}>
        <Trash2 className="size-4" />
        Nettoyer
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Suppression cascade</DialogTitle>
          <DialogDescription>
            Supprime les fichiers en doublon non suivis par Sonarr/Radarr et les torrents qBittorrent orphelins
            liés à ce média.
          </DialogDescription>
        </DialogHeader>

        {!result && (
          <>
            {previewMutation.isPending && (
              <div className="text-muted-foreground flex items-center gap-2 py-4 text-sm">
                <Loader2 className="size-4 animate-spin" /> Calcul de l'aperçu...
              </div>
            )}

            {preview && preview.items.length === 0 && (
              <p className="text-muted-foreground py-4 text-sm">Rien à supprimer pour ce média.</p>
            )}

            {preview && preview.items.length > 0 && (
              <div className="space-y-2">
                <ul className="max-h-64 space-y-1 overflow-y-auto text-sm">
                  {preview.items.map((item, i) => (
                    <li key={i} className="border-border flex items-start justify-between gap-2 border-b py-1.5 last:border-0">
                      <div>
                        <p className="text-muted-foreground text-xs">{KIND_LABELS[item.kind] ?? item.kind}</p>
                        <p className="break-all">{item.label}</p>
                      </div>
                      <span className="text-muted-foreground shrink-0 text-xs">{formatBytes(item.size)}</span>
                    </li>
                  ))}
                </ul>
                <p className="text-sm font-medium">Total récupérable : {formatBytes(preview.total_reclaimable_bytes)}</p>
              </div>
            )}
          </>
        )}

        {result && (
          <ul className="max-h-64 space-y-1 overflow-y-auto text-sm">
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
          {!result && preview && preview.items.length > 0 && (
            <Button
              type="button"
              variant="destructive"
              disabled={executeMutation.isPending}
              onClick={handleConfirm}
            >
              {executeMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <AlertTriangle className="size-4" />
              )}
              Confirmer la suppression
            </Button>
          )}
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            Fermer
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
