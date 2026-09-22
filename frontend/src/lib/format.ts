import { getLanguage, getLocale } from "@/i18n"

// Fuseau d'affichage choisi dans les préférences (vide = celui du navigateur).
// Stocké en module : les formats servent aussi hors composants React.
let displayTimeZone = ""

export function setDisplayTimeZone(timezone: string): void {
  displayTimeZone = timezone
}

function dateOptions(base: Intl.DateTimeFormatOptions): Intl.DateTimeFormatOptions {
  return displayTimeZone ? { ...base, timeZone: displayTimeZone } : base
}

/** Date renvoyée par l'API. Les valeurs sans fuseau sont de l'UTC (convention
 * de toutes les tables, voir backend) : sans ce "Z", le navigateur les lisait
 * comme de l'heure locale et affichait l'heure du conteneur, souvent décalée. */
export function parseApiDate(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(iso)
  const date = new Date(hasZone ? iso : `${iso}Z`)
  return Number.isNaN(date.getTime()) ? null : date
}

function safeFormat(date: Date, options: Intl.DateTimeFormatOptions): string {
  try {
    return date.toLocaleString(getLocale(), dateOptions(options))
  } catch {
    // Fuseau inconnu du navigateur : on retombe sur le sien plutôt que de
    // casser l'affichage.
    return date.toLocaleString(getLocale(), options)
  }
}

const BYTE_UNITS = {
  fr: ["o", "Ko", "Mo", "Go", "To"],
  en: ["B", "KB", "MB", "GB", "TB"],
}

export function formatBytes(bytes: number | null | undefined): string {
  const units = BYTE_UNITS[getLanguage()]
  if (!bytes || bytes <= 0) return `0 ${units[0]}`
  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`
}

export function formatDate(iso: string | null | undefined): string | null {
  const date = parseApiDate(iso)
  return date ? safeFormat(date, { day: "2-digit", month: "2-digit", year: "numeric" }) : null
}

export function formatDateTime(iso: string | null | undefined): string | null {
  const date = parseApiDate(iso)
  return date
    ? safeFormat(date, {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : null
}

const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
]

// "il y a 3 jours" / "3 days ago", dans la langue de l'interface.
export function formatRelativeTime(iso: string | null | undefined): string | null {
  const date = parseApiDate(iso)
  if (date === null) return null
  const seconds = Math.round((date.getTime() - Date.now()) / 1000)
  const formatter = new Intl.RelativeTimeFormat(getLocale(), { numeric: "auto" })
  for (const [unit, size] of RELATIVE_UNITS) {
    if (Math.abs(seconds) >= size) return formatter.format(Math.trunc(seconds / size), unit)
  }
  return formatter.format(0, "minute")
}

// "0.14.1" -> "v0.14.1" ; un build hors release ("dev") reste tel quel.
export function formatVersion(version: string): string {
  return /^\d+\.\d+\.\d+$/.test(version) ? `v${version}` : version
}

export function formatRatio(ratio: number | null | undefined): string | null {
  if (ratio === null || ratio === undefined) return null
  return ratio.toFixed(2)
}
