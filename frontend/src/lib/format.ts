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

// "0.14.1" -> "v0.14.1" ; un build hors release ("dev") reste tel quel.
export function formatVersion(version: string): string {
  return /^\d+\.\d+\.\d+$/.test(version) ? `v${version}` : version
}

export function formatRatio(ratio: number | null | undefined): string | null {
  if (ratio === null || ratio === undefined) return null
  return ratio.toFixed(2)
}
