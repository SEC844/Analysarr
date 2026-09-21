import { Loader2, RefreshCw } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { useRescanMediaMutation } from "@/hooks/use-media"
import { useI18n } from "@/i18n"

/** Analyse ciblée : relit Sonarr/Radarr, le serveur multimédia, la file
 * d'attente et les torrents de ce seul média, sans scanner la bibliothèque
 * entière. La fiche garde son identifiant, sauf si le média n'est plus suivi
 * par Sonarr/Radarr — elle disparaît alors, comme après une suppression. */
export function RescanMediaButton({ mediaId, onMediaDeleted }: { mediaId: number; onMediaDeleted: () => void }) {
  const { t } = useI18n()
  const rescan = useRescanMediaMutation()

  function handleClick() {
    rescan.mutate(mediaId, {
      onSuccess: (result) => {
        if (result.media_deleted) {
          toast.success(t("rescan.deleted"))
          onMediaDeleted()
          return
        }
        toast.success(t("rescan.done", { files: result.files, torrents: result.torrents }))
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : t("common.saveFailed")),
    })
  }

  return (
    <Button type="button" variant="secondary" disabled={rescan.isPending} onClick={handleClick} title={t("rescan.hint")}>
      {rescan.isPending ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
      {rescan.isPending ? t("rescan.running") : t("rescan.button")}
    </Button>
  )
}
