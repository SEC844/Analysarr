import { useEffect, useRef, useState, type PointerEvent } from "react"

import { useI18n } from "@/i18n"
import { byteTicks, dayFromIndex, dayIndex, dayTicks, formatDay, nearestIndex } from "@/lib/forecast"
import { formatBytes } from "@/lib/format"
import type { DiskForecast } from "@/types/forecast"

const HEIGHT = 200
const MARGIN = { top: 14, right: 12, bottom: 24, left: 60 }
const DEFAULT_WIDTH = 600

/** Largeur réelle du conteneur : le texte du graphique garde sa taille au
 * lieu d'être réduit avec un viewBox sur un téléphone. */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(DEFAULT_WIDTH)
  useEffect(() => {
    const element = ref.current
    if (!element || typeof ResizeObserver === "undefined") return
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(240, Math.round(entry.contentRect.width)))
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return { ref, width }
}

interface ChartPoint {
  day: string
  x: number
  used: number
  /** Projection : fourchette basse et haute. */
  low?: number
  high?: number
}

function pathOf(points: { x: number; y: number }[]): string {
  return points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join("")
}

/** Espace occupé d'un disque : historique en trait plein, projection en
 * tirets avec sa fourchette, capacité en ligne de référence. Une seule
 * série : pas de légende, le titre de la carte la nomme. */
export function DiskChart({ disk }: { disk: DiskForecast }) {
  const { t } = useI18n()
  const { ref, width } = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const total = disk.total ?? Math.max(1, ...disk.history.map((p) => p.used))

  // La projection commence au dernier jour mesuré : on ne le duplique pas.
  const points: ChartPoint[] = [
    ...disk.history.map((p) => ({ day: p.day, x: dayIndex(p.day), used: p.used })),
    ...disk.projection.slice(1).map((p) => ({ day: p.day, x: dayIndex(p.day), used: p.used, low: p.low, high: p.high })),
  ]
  if (points.length === 0) return null

  const first = points[0]?.x ?? 0
  const last = points[points.length - 1]?.x ?? first + 1
  const innerWidth = width - MARGIN.left - MARGIN.right
  const innerHeight = HEIGHT - MARGIN.top - MARGIN.bottom
  const sx = (x: number) => MARGIN.left + ((x - first) / Math.max(1, last - first)) * innerWidth
  const sy = (bytes: number) => MARGIN.top + innerHeight - (Math.min(bytes, total) / total) * innerHeight

  const historyPath = pathOf(disk.history.map((p) => ({ x: sx(dayIndex(p.day)), y: sy(p.used) })))
  const projection = disk.projection.map((p) => ({ ...p, x: sx(dayIndex(p.day)) }))
  const projectionPath = pathOf(projection.map((p) => ({ x: p.x, y: sy(p.used) })))
  const band =
    projection.length > 1
      ? `${pathOf(projection.map((p) => ({ x: p.x, y: sy(p.high) })))}${[...projection]
          .reverse()
          .map((p) => `L${p.x.toFixed(1)},${sy(p.low).toFixed(1)}`)
          .join("")}Z`
      : null
  const ticks = byteTicks(total)
  const xLabels = dayTicks(first, last, width < 420 ? 3 : 5)
  const active = hover === null ? null : points[hover]
  const positions = points.map((p) => sx(p.x))

  const onMove = (event: PointerEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    setHover(nearestIndex(positions, MARGIN.left + event.clientX - box.left))
  }

  return (
    <div ref={ref} className="relative">
      <svg
        width={width}
        height={HEIGHT}
        role="img"
        aria-label={t("forecast.chart.label")}
        className="block max-w-full overflow-visible text-sky-600"
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={MARGIN.left} x2={width - MARGIN.right} y1={sy(tick)} y2={sy(tick)} className="stroke-border" />
            <text
              x={MARGIN.left - 6}
              y={sy(tick)}
              textAnchor="end"
              dominantBaseline="middle"
              className="fill-muted-foreground text-[10px] tabular-nums"
            >
              {formatBytes(tick)}
            </text>
          </g>
        ))}
        {disk.total !== null && (
          <g>
            <line
              x1={MARGIN.left}
              x2={width - MARGIN.right}
              y1={sy(total)}
              y2={sy(total)}
              strokeDasharray="4 3"
              className="stroke-muted-foreground"
            />
            <text x={MARGIN.left + 4} y={sy(total) - 4} className="fill-muted-foreground text-[10px]">
              {t("forecast.chart.capacity", { size: formatBytes(total) })}
            </text>
          </g>
        )}
        {band && <path d={band} fill="currentColor" fillOpacity={0.1} stroke="none" />}
        {projection.length > 1 && (
          <path d={projectionPath} fill="none" stroke="currentColor" strokeWidth={2} strokeDasharray="5 4" />
        )}
        <path d={historyPath} fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" />
        {xLabels.map((tick) => (
          <text
            key={tick}
            x={sx(tick)}
            y={HEIGHT - 6}
            textAnchor={tick === first ? "start" : tick === last ? "end" : "middle"}
            className="fill-muted-foreground text-[10px] tabular-nums"
          >
            {formatDay(dayFromIndex(tick))}
          </text>
        ))}
        {active && (
          <g pointerEvents="none">
            <line
              x1={sx(active.x)}
              x2={sx(active.x)}
              y1={MARGIN.top}
              y2={MARGIN.top + innerHeight}
              className="stroke-muted-foreground"
            />
            <circle cx={sx(active.x)} cy={sy(active.used)} r={4} fill="currentColor" className="stroke-background" strokeWidth={2} />
          </g>
        )}
        <rect
          x={MARGIN.left}
          y={MARGIN.top}
          width={innerWidth}
          height={innerHeight}
          fill="transparent"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
      {active && (
        <div
          className="bg-popover text-popover-foreground pointer-events-none absolute top-1 z-10 rounded-md border px-2.5 py-1.5 text-xs shadow-md"
          style={
            sx(active.x) > width / 2
              ? { right: width - sx(active.x) + 8 }
              : { left: sx(active.x) + 8 }
          }
        >
          <p className="font-medium">{formatDay(active.day, true)}</p>
          {active.low === undefined ? (
            <p className="tabular-nums">{t("forecast.chart.used", { size: formatBytes(active.used) })}</p>
          ) : (
            <>
              <p className="tabular-nums">{t("forecast.chart.expected", { size: formatBytes(active.used) })}</p>
              <p className="text-muted-foreground tabular-nums">
                {t("forecast.chart.range", { low: formatBytes(active.low), high: formatBytes(active.high ?? active.used) })}
              </p>
            </>
          )}
        </div>
      )}
    </div>
  )
}
