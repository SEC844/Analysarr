import { useState } from "react"
import { Loader2, Search } from "lucide-react"

import { Button } from "@/components/ui/button"
import { useCleanupOtherLinksQuery } from "@/hooks/use-cleanup"
import { useI18n } from "@/i18n"
import { formatBytes } from "@/lib/format"
import type { CleanupSpace } from "@/types/cleanup"

/** Espace occupé contre espace réellement libéré. Quand un lien vit hors du
 * média (torrent non rattaché, lien laissé par cross-seed…), le supprimer ne
 * libère pas cet espace : on le dit, et on peut retrouver où vit ce lien
 * (parcours du disque à la demande, côté serveur). */
export function SpaceDetails({ mediaId, space }: { mediaId: number; space: CleanupSpace }) {
  const { t } = useI18n()
  const [searching, setSearching] = useState(false)
  const links = useCleanupOtherLinksQuery(mediaId, searching)
  const roots = links.data?.roots.join(", ") ?? ""
  // Aucun fichier connu sur le disque : rien à expliquer.
  if (space.on_disk_bytes === 0) return null

  return (
    <div className="space-y-1.5 border-t px-1 pt-2 text-xs">
      <p className="flex justify-between gap-2">
        <span className="text-muted-foreground">{t("cleanup.space.freed")}</span>
        <span className="font-medium tabular-nums">
          {t("cleanup.space.ofTotal", { freed: formatBytes(space.freed_bytes), total: formatBytes(space.on_disk_bytes) })}
        </span>
      </p>
      {space.held_bytes > 0 && (
        <>
          <p className="text-amber-700 dark:text-amber-300">
            {t("cleanup.space.held", { count: space.external_links, size: formatBytes(space.held_bytes) })}
          </p>
          {!searching ? (
            <Button type="button" variant="outline" size="sm" className="h-7 text-xs" onClick={() => setSearching(true)}>
              <Search className="size-3.5" />
              {t("cleanup.space.find")}
            </Button>
          ) : links.isLoading ? (
            <p className="text-muted-foreground flex items-center gap-1.5">
              <Loader2 className="size-3.5 animate-spin" />
              {t("cleanup.space.searching")}
            </p>
          ) : links.isError ? (
            <p className="text-destructive">{t("cleanup.space.failed")}</p>
          ) : links.data && links.data.paths.length === 0 ? (
            <p className="text-muted-foreground">
              {links.data.complete ? t("cleanup.space.none", { roots }) : t("cleanup.space.incomplete")}
            </p>
          ) : links.data ? (
            <div className="space-y-1">
              <p className="text-muted-foreground">{t("cleanup.space.found", { roots })}</p>
              <ul className="space-y-0.5">
                {links.data.paths.map((path) => (
                  <li key={path} className="truncate font-mono text-[11px]" title={path}>
                    {path}
                  </li>
                ))}
              </ul>
              {!links.data.complete && <p className="text-muted-foreground">{t("cleanup.space.incomplete")}</p>}
            </div>
          ) : null}
        </>
      )}
    </div>
  )
}
