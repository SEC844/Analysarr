import { useState } from "react"
import { HardDrive, Info } from "lucide-react"

import { DiskChart } from "@/components/forecast/disk-chart"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useForecastQuery } from "@/hooks/use-cleanup"
import { useI18n } from "@/i18n"
import { fillText, formatDay, monthlyGrowth } from "@/lib/forecast"
import { formatBytes } from "@/lib/format"
import type { DiskForecast, Forecast, Trend } from "@/types/forecast"

function Stat({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="text-sm font-medium tabular-nums">{value}</dd>
      {detail && <dd className="text-muted-foreground text-xs tabular-nums">{detail}</dd>}
    </div>
  )
}

function useGrowthText() {
  const { t } = useI18n()
  return (trend: Trend | null) => {
    if (!trend) return { value: t("forecast.noTrend"), detail: undefined }
    const growth = monthlyGrowth(trend)
    return {
      value: t("forecast.growth", { value: growth.value }),
      detail: t("forecast.growthRange", { low: growth.low, high: growth.high }),
    }
  }
}

/** Historique échantillonné une semaine sur l'autre (depuis aujourd'hui),
 * puis la projection : la même information que le graphique, lisible au
 * clavier et par un lecteur d'écran. */
function DiskTable({ disk }: { disk: DiskForecast }) {
  const { t } = useI18n()
  const history = disk.history.filter((_, i) => (disk.history.length - 1 - i) % 7 === 0)
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs tabular-nums">
        <thead className="text-muted-foreground text-left">
          <tr>
            <th className="py-1 pr-4 font-normal">{t("forecast.table.day")}</th>
            <th className="py-1 pr-4 font-normal">{t("forecast.table.used")}</th>
            <th className="py-1 font-normal">{t("forecast.table.range")}</th>
          </tr>
        </thead>
        <tbody className="divide-border divide-y">
          {history.map((point) => (
            <tr key={point.day}>
              <td className="py-1 pr-4">{formatDay(point.day, true)}</td>
              <td className="py-1 pr-4">{formatBytes(point.used)}</td>
              <td className="py-1" />
            </tr>
          ))}
          {disk.projection.slice(1).map((point) => (
            <tr key={point.day} className="text-muted-foreground">
              <td className="py-1 pr-4">
                {formatDay(point.day, true)} ({t("forecast.table.expected")})
              </td>
              <td className="py-1 pr-4">{formatBytes(point.used)}</td>
              <td className="py-1">
                {formatBytes(point.low)} – {formatBytes(point.high)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DiskCard({ disk }: { disk: DiskForecast }) {
  const { t } = useI18n()
  const growthText = useGrowthText()
  const [table, setTable] = useState(false)
  const title = disk.roles.map((role) => t(`forecast.roles.${role}`)).join(" · ")
  const growth = growthText(disk.trend)
  const percent = disk.total && disk.used !== null ? Math.round((disk.used / disk.total) * 100) : null

  return (
    <article className="border-border space-y-3 rounded-lg border p-4">
      <header className="flex items-start gap-2">
        <HardDrive className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        <div className="min-w-0">
          <h3 className="text-sm font-medium">{title}</h3>
          <p className="text-muted-foreground truncate font-mono text-xs" title={disk.paths.join(", ")}>
            {disk.paths.join(", ")}
          </p>
        </div>
      </header>
      {!disk.available ? (
        <p className="text-sm text-amber-700 dark:text-amber-400">{t("forecast.unavailable")}</p>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-3">
            <Stat
              label={t("forecast.used")}
              value={formatBytes(disk.used)}
              detail={
                percent !== null
                  ? t("forecast.usedValue", { total: formatBytes(disk.total), percent })
                  : undefined
              }
            />
            <Stat label={t("forecast.free")} value={formatBytes(disk.free)} />
            <Stat label={t("forecast.growthLabel")} value={growth.value} detail={growth.detail} />
            <Stat
              label={t("forecast.fillLabel")}
              value={fillText(t, disk) ?? t("forecast.noTrend")}
              detail={
                disk.fill
                  ? disk.fill.latest_date
                    ? `${formatDay(disk.fill.earliest_date, true)} – ${formatDay(disk.fill.latest_date, true)}`
                    : formatDay(disk.fill.earliest_date, true)
                  : undefined
              }
            />
          </dl>
          <DiskChart disk={disk} />
          <Button type="button" variant="ghost" size="sm" onClick={() => setTable((shown) => !shown)}>
            {table ? t("forecast.table.hide") : t("forecast.table.show")}
          </Button>
          {table && <DiskTable disk={disk} />}
        </>
      )}
    </article>
  )
}

function Content({ forecast }: { forecast: Forecast }) {
  const { t } = useI18n()
  const growthText = useGrowthText()
  if (!forecast.latest_day) return <p className="text-muted-foreground text-sm">{t("forecast.empty")}</p>
  const library = forecast.library
  const growth = growthText(library?.trend ?? null)

  return (
    <div className="space-y-4">
      {!forecast.enough_history && (
        <p className="bg-muted/60 flex items-start gap-2 rounded-md px-3 py-2 text-sm" role="status">
          <Info className="text-muted-foreground mt-0.5 size-4 shrink-0" />
          {t("forecast.notEnough", { days: forecast.history_days, min: forecast.min_history_days })}
        </p>
      )}
      {library && (
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label={t("forecast.library")} value={t("forecast.librarySize", { size: formatBytes(library.size) })} />
          <Stat label={t("forecast.growthLabel")} value={growth.value} detail={growth.detail} />
        </dl>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {forecast.disks.map((disk) => (
          <DiskCard key={disk.key} disk={disk} />
        ))}
      </div>
    </div>
  )
}

/** Section dédiée de la page Nettoyage : historique de chaque disque,
 * projection et date de remplissage (backend : services/forecast.py). */
export function ForecastSection() {
  const { t } = useI18n()
  const { data, isLoading, isError } = useForecastQuery()

  return (
    <section aria-labelledby="forecast-title" className="space-y-3">
      <div className="space-y-1">
        <h2 id="forecast-title" className="text-lg font-semibold tracking-tight">
          {t("forecast.title")}
        </h2>
        <p className="text-muted-foreground text-sm">{t("forecast.description", { days: data?.window_days ?? 30 })}</p>
      </div>
      {isLoading && <Skeleton className="h-48 w-full" />}
      {isError && <p className="text-destructive text-sm">{t("forecast.loadFailed")}</p>}
      {data && <Content forecast={data} />}
    </section>
  )
}
