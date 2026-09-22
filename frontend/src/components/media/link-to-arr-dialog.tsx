import { useState } from "react"
import { Copy, Link } from "lucide-react"
import { toast } from "sonner"

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
import { useI18n } from "@/i18n"
import { copyText } from "@/lib/clipboard"
import type { MediaDetail } from "@/types/media"

/** Média présent dans la bibliothèque mais suivi par aucun Sonarr/Radarr : le
 * rattacher est une action manuelle côté Sonarr/Radarr (Analysarr n'écrit
 * jamais dans leur bibliothèque à leur place). Ce bouton donne la marche à
 * suivre et l'identifiant à coller. */
export function LinkToArrDialog({ media }: { media: MediaDetail }) {
  const { t, rich } = useI18n()
  const [open, setOpen] = useState(false)
  const isSeries = media.media_type === "series"
  const identifier = isSeries
    ? media.tvdb_id
      ? `${media.tvdb_id}`
      : media.imdb_id || (media.tmdb_id ? `${media.tmdb_id}` : "")
    : media.imdb_id || (media.tmdb_id ? `${media.tmdb_id}` : "")
  const identifierLabel = isSeries && media.tvdb_id ? "TVDB" : media.imdb_id ? "IMDb" : "TMDB"

  const search = identifier || media.title
  const folder = media.files[0]?.path ?? ""

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button
            type="button"
            className="bg-amber-500 text-amber-950 hover:bg-amber-500/90 dark:bg-amber-400 dark:hover:bg-amber-400/90"
          />
        }
      >
        <Link className="size-4" />
        {t(isSeries ? "untracked.buttonSeries" : "untracked.buttonMovie")}
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t(isSeries ? "untracked.buttonSeries" : "untracked.buttonMovie")}</DialogTitle>
          <DialogDescription>{t(isSeries ? "untracked.whySeries" : "untracked.whyMovie")}</DialogDescription>
        </DialogHeader>

        <ol className="list-decimal space-y-2 pl-5 text-sm">
          <li>{t(isSeries ? "untracked.step1Series" : "untracked.step1Movie")}</li>
          <li>
            {rich("untracked.step2", { query: <span className="font-medium">{search}</span> })}
            <div className="mt-1 flex items-center gap-2">
              <code className="bg-muted rounded px-1.5 py-0.5 text-xs">
                {identifier ? `${identifierLabel} ${identifier}` : media.title}
              </code>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                title={t("untracked.copy")}
                onClick={async () =>
                  (await copyText(search)) ? toast.success(t("untracked.copied")) : toast.error(t("untracked.copyFailed"))
                }
              >
                <Copy className="size-4" />
              </Button>
            </div>
          </li>
          <li>{t("untracked.step3")}</li>
          <li>{t("untracked.step4")}</li>
        </ol>

        {folder && (
          <p className="text-muted-foreground text-xs">
            {rich("untracked.folder", { path: <code className="bg-muted rounded px-1 py-0.5">{folder}</code> })}
          </p>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            {t("common.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
