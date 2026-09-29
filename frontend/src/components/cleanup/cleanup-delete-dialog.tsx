import { useState } from "react"
import { AlertTriangle, CheckCircle2, CircleDashed, Loader2, ShieldCheck, XCircle } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { usePreferences } from "@/hooks/use-app"
import { useI18n } from "@/i18n"
import { deleteSelectionExecute, getCleanupCandidate, getMedia } from "@/lib/api"
import { selectionSize } from "@/lib/cleanup"
import { formatBytes } from "@/lib/format"
import type { CleanupCandidate } from "@/types/cleanup"

type Outcome = "pending" | "running" | "done" | "skipped" | "failed"

const ICONS: Record<Outcome, typeof CheckCircle2> = {
  pending: CircleDashed,
  running: Loader2,
  done: CheckCircle2,
  skipped: ShieldCheck,
  failed: XCircle,
}

/** Supprime chaque média de la sélection EN ENTIER par la suppression
 * sélective existante (« Tout supprimer » : même route, même transaction
 * tout ou rien, même corbeille) — aucun chemin de suppression propre à
 * l'assistant. Juste avant chaque suppression, le média est réévalué : s'il
 * est devenu protégé (quelqu'un l'a mis en favori, commencé à le regarder),
 * il est laissé de côté. */
async function deleteWholeMedia(candidate: CleanupCandidate, removeFromArr: boolean): Promise<Outcome> {
  const current = await getCleanupCandidate(candidate.media_id)
  if (current.protections.length > 0) return "skipped"
  const media = await getMedia(candidate.media_id)
  const tracked = media.radarr_id !== null || media.sonarr_id !== null
  const result = await deleteSelectionExecute(candidate.media_id, {
    torrent_ids: media.torrents.map((torrent) => torrent.id),
    media_file_ids: media.files.map((file) => file.id),
    // Rien à retirer pour un média qu'aucun Sonarr/Radarr ne suit.
    remove_from_arr: removeFromArr && tracked,
  })
  return result.steps.every((step) => step.success) ? "done" : "failed"
}

export function CleanupDeleteDialog({
  candidates,
  open,
  onClose,
  onDeleted,
}: {
  candidates: CleanupCandidate[]
  open: boolean
  onClose: () => void
  onDeleted: (ids: number[]) => void
}) {
  const { t } = useI18n()
  const prefs = usePreferences()
  const queryClient = useQueryClient()
  const [removeFromArr, setRemoveFromArr] = useState(prefs.delete_remove_from_arr_default)
  const [outcomes, setOutcomes] = useState<Record<number, Outcome> | null>(null)
  // Liste figée au lancement : les médias supprimés quittent la sélection
  // pendant l'exécution, le récapitulatif doit pourtant tous les montrer.
  const [batch, setBatch] = useState<CleanupCandidate[] | null>(null)
  const shown = batch ?? candidates
  const running = outcomes !== null && Object.values(outcomes).some((o) => o === "pending" || o === "running")
  const finished = outcomes !== null && !running

  const close = () => {
    if (running) return
    setOutcomes(null)
    setBatch(null)
    onClose()
  }

  const run = async () => {
    const state: Record<number, Outcome> = Object.fromEntries(candidates.map((c) => [c.media_id, "pending"]))
    setBatch(candidates)
    setOutcomes({ ...state })
    // Un média après l'autre : chaque suppression est une transaction
    // complète, et une erreur n'arrête pas les suivantes.
    for (const candidate of candidates) {
      state[candidate.media_id] = "running"
      setOutcomes({ ...state })
      try {
        state[candidate.media_id] = await deleteWholeMedia(candidate, removeFromArr)
      } catch {
        state[candidate.media_id] = "failed"
      }
      setOutcomes({ ...state })
    }
    onDeleted(candidates.filter((c) => state[c.media_id] === "done").map((c) => c.media_id))
    queryClient.invalidateQueries({ queryKey: ["media"] })
    queryClient.invalidateQueries({ queryKey: ["cleanup"] })
    queryClient.invalidateQueries({ queryKey: ["history"] })
  }

  const doneCount = outcomes ? Object.values(outcomes).filter((o) => o === "done").length : 0

  return (
    <Dialog open={open} onOpenChange={(value) => !value && close()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("cleanup.delete.title")}</DialogTitle>
          <DialogDescription>{t("cleanup.delete.description")}</DialogDescription>
        </DialogHeader>

        <ul className="max-h-72 min-w-0 space-y-1 overflow-y-auto text-sm">
          {shown.map((candidate) => {
            const outcome = outcomes?.[candidate.media_id]
            const Icon = outcome ? ICONS[outcome] : null
            return (
              <li key={candidate.media_id} className="flex items-center gap-2 py-1">
                {Icon && (
                  <Icon
                    className={
                      outcome === "running"
                        ? "size-4 shrink-0 animate-spin"
                        : outcome === "done"
                          ? "size-4 shrink-0 text-emerald-500"
                          : outcome === "failed"
                            ? "text-destructive size-4 shrink-0"
                            : "text-muted-foreground size-4 shrink-0"
                    }
                    aria-label={t(`cleanup.delete.${outcome ?? "pending"}`)}
                  />
                )}
                <span className="min-w-0 flex-1 truncate" title={candidate.title}>
                  {candidate.title}
                  {candidate.year ? ` (${candidate.year})` : ""}
                </span>
                {outcome === "skipped" || outcome === "failed" ? (
                  <span className="text-muted-foreground shrink-0 text-xs">{t(`cleanup.delete.${outcome}`)}</span>
                ) : (
                  <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                    {formatBytes(candidate.reclaimable_bytes)}
                  </span>
                )}
              </li>
            )
          })}
        </ul>

        {!outcomes && (
          <>
            <p className="text-sm font-medium">{t("cleanup.delete.total", { size: selectionSize(candidates) })}</p>
            <div className="flex items-start gap-2">
              <Checkbox
                id="cleanup-remove-arr"
                checked={removeFromArr}
                onCheckedChange={(checked) => setRemoveFromArr(checked)}
              />
              <Label htmlFor="cleanup-remove-arr" className="text-sm leading-snug font-normal">
                {t("cleanup.delete.removeFromArr")}
              </Label>
            </div>
          </>
        )}
        {finished && (
          <p className="text-sm font-medium" role="status">
            {t("cleanup.delete.finished", { done: doneCount, total: shown.length })}
          </p>
        )}

        <DialogFooter>
          {!outcomes && (
            <Button type="button" variant="destructive" disabled={candidates.length === 0} onClick={() => void run()}>
              <AlertTriangle className="size-4" />
              {t("common.confirmDelete")}
            </Button>
          )}
          <Button type="button" variant="outline" disabled={running} onClick={close}>
            {t("common.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
