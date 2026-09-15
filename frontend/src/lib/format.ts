import { getLanguage, getLocale } from "@/i18n"

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
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleDateString(getLocale(), { day: "2-digit", month: "2-digit", year: "numeric" })
}

export function formatDateTime(iso: string | null | undefined): string | null {
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleString(getLocale(), {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
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
  if (!iso) return null
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
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
