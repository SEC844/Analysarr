import { getLocale } from "@/i18n"
import type { I18nContextValue } from "@/i18n/core"
import { formatBytes } from "@/lib/format"
import type { DiskForecast, FillEstimate, Trend } from "@/types/forecast"

type Translate = I18nContextValue["t"]

const DAY_MS = 24 * 3600 * 1000

/** Jour de calendrier « AAAA-MM-JJ » : minuit LOCAL, jamais de l'UTC (une
 * date sans heure n'a pas de fuseau ; lue en UTC, elle reculait d'un jour à
 * l'ouest de Greenwich). */
export function parseDay(day: string): Date {
  const [year = 1970, month = 1, date = 1] = day.split("-").map(Number)
  return new Date(year, month - 1, date)
}

export function dayIndex(day: string): number {
  return Math.round(parseDay(day).getTime() / DAY_MS)
}

/** Inverse de `dayIndex`. Midi UTC : le même jour local sous tout fuseau
 * à moins de douze heures de Greenwich. */
export function dayFromIndex(index: number): string {
  const date = new Date(index * DAY_MS + DAY_MS / 2)
  const pad = (value: number) => String(value).padStart(2, "0")
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export function formatDay(day: string, withYear = false): string {
  return parseDay(day).toLocaleDateString(getLocale(), {
    day: "2-digit",
    month: "2-digit",
    ...(withYear ? { year: "numeric" } : {}),
  })
}

// --- Durées ---------------------------------------------------------------------

type DurationUnit = "days" | "weeks" | "months" | "years"

/** Unité lisible : jours sous deux semaines, semaines sous six mois, mois
 * sous deux ans, années au-delà. */
export function durationUnit(days: number): { unit: DurationUnit; value: number } {
  if (days < 14) return { unit: "days", value: Math.max(0, Math.round(days)) }
  if (days < 183) return { unit: "weeks", value: Math.round(days / 7) }
  if (days < 730) return { unit: "months", value: Math.round(days / 30.44) }
  return { unit: "years", value: Math.round(days / 365.25) }
}

function duration(t: Translate, days: number): string {
  const { unit, value } = durationUnit(days)
  return t(`forecast.units.${unit}`, { count: value })
}

/** « Plein dans 6 à 9 semaines », « dans 3 semaines au plus tôt »… */
export function fillText(t: Translate, disk: DiskForecast): string | null {
  if (!disk.trend) return null
  const fill: FillEstimate | null = disk.fill
  if (!fill) return t("forecast.fill.never")
  if (fill.earliest_days < 1) return t("forecast.fill.now")
  if (fill.latest_days === null) return t("forecast.fill.atEarliest", { duration: duration(t, fill.earliest_days) })
  const from = durationUnit(fill.earliest_days)
  const to = durationUnit(fill.latest_days)
  if (from.unit === to.unit && from.value === to.value) {
    return t("forecast.fill.about", { duration: duration(t, fill.earliest_days) })
  }
  return t("forecast.fill.range", {
    from: from.unit === to.unit ? String(from.value) : duration(t, fill.earliest_days),
    to: duration(t, fill.latest_days),
  })
}

// --- Croissance -----------------------------------------------------------------

const DAYS_PER_MONTH = 30.44

/** Taille signée : « +12.0 Go », « −3.5 Go » (formatBytes ne connaît que le
 * positif). */
export function signedBytes(bytes: number): string {
  const rounded = Math.round(bytes)
  if (rounded === 0) return formatBytes(0)
  return `${rounded > 0 ? "+" : "−"}${formatBytes(Math.abs(rounded))}`
}

/** Croissance par mois, avec sa fourchette. */
export function monthlyGrowth(trend: Trend): { value: string; low: string; high: string } {
  return {
    value: signedBytes(trend.per_day * DAYS_PER_MONTH),
    low: signedBytes(trend.low * DAYS_PER_MONTH),
    high: signedBytes(trend.high * DAYS_PER_MONTH),
  }
}

// --- Échelles du graphique --------------------------------------------------------

const BYTE_STEP = 1024

/** Graduations « rondes » de 0 à `max` dans l'unité affichée (Go, To…), au
 * plus `count` + 1 valeurs. */
export function byteTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0]
  let unit = 1
  while (max / unit >= BYTE_STEP) unit *= BYTE_STEP
  const raw = max / unit / count
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const step = ([1, 2, 2.5, 5, 10].find((m) => m * magnitude >= raw) ?? 10) * magnitude * unit
  const ticks: number[] = []
  for (let value = 0; value <= max * (1 + 1e-9); value += step) ticks.push(value)
  return ticks
}

/** `count` jours régulièrement espacés de `first` à `last` (inclus), en
 * indices de jour : l'axe des dates suit le temps, pas le nombre de points
 * (quotidiens dans l'historique, hebdomadaires dans la projection). */
export function dayTicks(first: number, last: number, count: number): number[] {
  if (last <= first) return [first]
  const span = last - first
  const ticks = Array.from({ length: count }, (_, i) => first + Math.round((i * span) / (count - 1)))
  return [...new Set(ticks)]
}

/** Point le plus proche de `target` dans une liste triée de positions. */
export function nearestIndex(positions: number[], target: number): number {
  let best = 0
  for (let i = 1; i < positions.length; i += 1) {
    if (Math.abs((positions[i] ?? 0) - target) < Math.abs((positions[best] ?? 0) - target)) best = i
  }
  return best
}
