import { useEffect, useState } from "react"
import { CheckCircle2, Link2, Loader2, XCircle } from "lucide-react"

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
import { useHardlinkRepairExecuteMutation, useHardlinkRepairPreviewMutation } from "@/hooks/use-media"
import { useI18n } from "@/i18n"
import { formatBytes } from "@/lib/format"
import type { HardlinkRepairPreview, HardlinkRepairResult } from "@/types/media"

export function HardlinkRepairDialog({ mediaId }: { mediaId: number }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<HardlinkRepairPreview | null>(null)
  const [result, setResult] = useState<HardlinkRepairResult | null>(null)

  const previewMutation = useHardlinkRepairPreviewMutation()
  const executeMutation = useHardlinkRepairExecuteMutation()

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
      <DialogTrigger render={<Button variant="secondary" />}>
        <Link2 className="size-4" />
        {t("repair.button")}
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("repair.title")}</DialogTitle>
          <DialogDescription>{t("repair.description")}</DialogDescription>
        </DialogHeader>

        {!result && (
          <>
            {previewMutation.isPending && (
              <div className="text-muted-foreground flex items-center gap-2 py-4 text-sm">
                <Loader2 className="size-4 animate-spin" /> {t("common.previewing")}
              </div>
            )}

            {preview && preview.items.length === 0 && (
              <p className="text-muted-foreground py-4 text-sm">{t("repair.nothing")}</p>
            )}

            {preview && preview.items.length > 0 && (
              <ul className="max-h-64 space-y-1 overflow-y-auto text-sm">
                {preview.items.map((item) => (
                  <li
                    key={`${item.torrent_id}-${item.media_file_id}`}
                    className="border-border flex items-start justify-between gap-2 border-b py-1.5 last:border-0"
                  >
                    <div className="min-w-0">
                      <p className="text-muted-foreground text-xs">
                        {item.episode_label ?? t("repair.file")} —{" "}
                        {item.direction === "torrent_to_library"
                          ? t("repair.fromTorrent", { name: item.torrent_name })
                          : t("repair.joinsLibrary", { name: item.torrent_name })}
                      </p>
                      <p className="break-all">{item.target_path}</p>
                    </div>
                    <span className="text-muted-foreground shrink-0 text-xs">{formatBytes(item.size)}</span>
                  </li>
                ))}
              </ul>
            )}

            {preview && preview.unmatched_torrents.length > 0 && (
              <p className="text-muted-foreground text-xs">
                {t("repair.unmatched", { list: preview.unmatched_torrents.join(", ") })}
              </p>
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
                  {step.success && step.used_symlink && (
                    <p className="text-muted-foreground text-xs">{t("repair.symlinkHint")}</p>
                  )}
                  {step.error && <p className="text-destructive text-xs">{step.error}</p>}
                </div>
              </li>
            ))}
          </ul>
        )}

        <DialogFooter>
          {!result && preview && preview.items.length > 0 && (
            <Button type="button" disabled={executeMutation.isPending} onClick={handleConfirm}>
              {executeMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <Link2 className="size-4" />}
              {t("repair.confirm")}
            </Button>
          )}
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            {t("common.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
