import { CheckCircle2, RefreshCw, XCircle } from "lucide-react"
import { Link } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { useRefreshServicesStatusMutation, useServicesStatusQuery } from "@/hooks/use-services"
import { useI18n } from "@/i18n"
import { formatRelativeTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { ServiceKey } from "@/types/services"

// Section des Réglages de chaque service.
const SETTINGS_SECTIONS: Record<ServiceKey, string> = {
  emby: "emby",
  sonarr: "sonarr",
  radarr: "radarr",
  qbittorrent: "qbittorrent",
  cross_seed: "cross-seed",
  seer: "seer",
}

// En-tête : pastille verte si tous les services configurés répondent, rouge
// sinon ; le détail s'ouvre au clic.
export function ServiceStatusIndicator() {
  const { t } = useI18n()
  const { data } = useServicesStatusQuery()
  const refresh = useRefreshServicesStatusMutation()

  if (!data || data.services.length === 0) return null

  const downCount = data.services.filter((s) => !s.ok).length
  const summary = downCount === 0 ? t("servicesStatus.allOk") : t("servicesStatus.someDown", { count: downCount })

  return (
    <Popover>
      <PopoverTrigger
        render={
          <button
            type="button"
            aria-label={summary}
            title={summary}
            className="hover:bg-muted flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm transition-colors"
          />
        }
      >
        <span className={cn("size-2 rounded-full", downCount ? "bg-destructive" : "bg-emerald-500")} />
        <span className={cn("tabular-nums", downCount ? "text-destructive" : "text-muted-foreground")}>
          {data.services.length - downCount}/{data.services.length}
        </span>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <div className="flex items-start justify-between gap-2 px-1 pb-2">
          <div className="min-w-0">
            <p className="text-sm font-medium">{t("servicesStatus.title")}</p>
            <p className="text-muted-foreground text-xs">
              {summary} · {t("servicesStatus.checked", { time: formatRelativeTime(data.checked_at) ?? "—" })}
            </p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
            title={t("servicesStatus.refresh")}
            aria-label={t("servicesStatus.refresh")}
          >
            <RefreshCw className={cn("size-4", refresh.isPending && "animate-spin")} />
          </Button>
        </div>
        <ul className="divide-border divide-y border-t">
          {data.services.map((service, index) => (
            <li key={`${service.service}-${index}`}>
              <Link
                to={`/settings?section=${SETTINGS_SECTIONS[service.service] ?? "emby"}`}
                className="hover:bg-muted flex items-start gap-2 rounded-md px-1 py-2"
              >
                {service.ok ? (
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
                ) : (
                  <XCircle className="text-destructive mt-0.5 size-4 shrink-0" />
                )}
                <span className="min-w-0">
                  <span className="block text-sm">{service.name}</span>
                  <span className="text-muted-foreground block text-xs break-words line-clamp-2" title={service.message}>
                    {service.message}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  )
}
