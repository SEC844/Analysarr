import { ChevronDown, Loader2, RefreshCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useScanRunner } from "@/hooks/use-scan"
import { useSettingsQuery } from "@/hooks/use-settings"
import { useI18n, type MessageKey } from "@/i18n"
import type { ScanScope } from "@/types/media"

// Étapes envoyées par le backend (événements SSE) : identifiants fixes.
const STAGES = new Set([
  "radarr",
  "sonarr",
  "emby",
  "historique",
  "qbittorrent",
  "enregistrement",
  "visionnage",
  "seer",
  "file d'attente",
  "statuts",
  "torrents",
  "queue",
  "watch",
])

// Périmètres proposés par la flèche. L'analyse complète reste l'action du
// bouton lui-même : cliquer « Analyser » ne doit jamais ouvrir un menu.
const PARTIAL_SCOPES: ScanScope[] = ["library", "torrents", "queue", "watch", "seer"]

export function ScanButton() {
  const { t } = useI18n()
  const { start, isRunning, event } = useScanRunner()
  const { data: settings } = useSettingsQuery()

  const stageLabel =
    event?.type === "progress" && event.stage
      ? STAGES.has(event.stage)
        ? t(`scan.stages.${event.stage}` as MessageKey)
        : event.stage
      : t("scan.starting")

  // Seer n'est proposé que s'il est activé : même principe que partout
  // ailleurs, une fonctionnalité désactivée ne laisse aucune trace.
  const scopes = PARTIAL_SCOPES.filter((scope) => scope !== "seer" || settings?.seer.enabled)

  return (
    <div className="flex items-center gap-3">
      {isRunning && <span className="text-muted-foreground text-sm">{stageLabel}</span>}
      {!isRunning && event?.type === "failed" && (
        <span className="text-destructive text-sm">{t("scan.failed", { message: event.message ?? "" })}</span>
      )}
      {!isRunning && event?.type === "completed" && (
        <span className="text-muted-foreground text-sm">
          {t("scan.summary", {
            media: event.media_count ?? 0,
            duplicates: event.duplicate_count ?? 0,
            orphans: event.orphan_count ?? 0,
          })}
          {event.qbittorrent_torrent_count !== undefined && event.qbittorrent_matched_count !== undefined && (
            <>
              {" · "}
              <span
                title={t("scan.matchedHint")}
                className={
                  event.qbittorrent_torrent_count - event.qbittorrent_matched_count > 0
                    ? "text-amber-500 dark:text-amber-400"
                    : undefined
                }
              >
                {t("scan.matched", { matched: event.qbittorrent_matched_count, total: event.qbittorrent_torrent_count })}
              </span>
            </>
          )}
        </span>
      )}

      {/* Bouton scindé : l'action principale lance l'analyse complète, la
          flèche ouvre les analyses ciblées. */}
      <div className="flex items-center">
        <Button type="button" className="rounded-r-none" onClick={() => start("full")} disabled={isRunning}>
          {isRunning ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          {t("scan.button")}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                type="button"
                className="border-primary-foreground/20 rounded-l-none border-l px-2"
                disabled={isRunning}
                aria-label={t("scan.scopeMenu")}
              />
            }
          >
            <ChevronDown className="size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {scopes.map((scope) => (
              <DropdownMenuItem key={scope} onClick={() => start(scope)}>
                <span className="flex flex-col">
                  <span>{t(`scan.scopes.${scope}` as MessageKey)}</span>
                  <span className="text-muted-foreground text-xs">{t(`scan.scopeHelp.${scope}` as MessageKey)}</span>
                </span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}
