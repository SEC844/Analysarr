import { CheckCircle2, EyeOff, HardDriveDownload, Inbox, Link2, Search, XCircle } from "lucide-react"

import { TorrentOptions } from "@/components/media/ignore-actions"
import { Badge } from "@/components/ui/badge"
import { useI18n } from "@/i18n"
import { formatBytes, formatDate, formatRatio } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { TorrentRead } from "@/types/media"

/** Un torrent de la fiche média : état de protection, trackers et
 * statistiques de partage. Un torrent ignoré reste affiché, estompé. */
export function TorrentRow({ mediaId, torrent }: { mediaId: number; torrent: TorrentRead }) {
  const { t } = useI18n()
  return (
    <li className={cn("space-y-2 py-3", torrent.ignored && "opacity-60")}>
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
        <div className="flex shrink-0 items-center gap-1">
          <span className="text-muted-foreground">{formatBytes(torrent.size)}</span>
          <TorrentOptions mediaId={mediaId} torrent={torrent} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {torrent.ignored && (
          <Badge variant="outline" title={t("ignore.ignoredHint")}>
            <EyeOff className="size-3" /> {t("ignore.ignored")}
          </Badge>
        )}
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
        {torrent.is_hardlinked === false && !torrent.repairable && torrent.not_imported && (
          <Badge variant="outline" className="bg-muted text-muted-foreground border-transparent" title={t("media.notImportedHint")}>
            <Inbox className="size-3" /> {t("media.notImported")}
          </Badge>
        )}
        {torrent.is_hardlinked === false && !torrent.repairable && !torrent.not_imported && (
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
  )
}
