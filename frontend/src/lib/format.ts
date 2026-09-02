export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return "0 o"
  const units = ["o", "Ko", "Mo", "Go", "To"]
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
  return date.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric" })
}

export function formatRatio(ratio: number | null | undefined): string | null {
  if (ratio === null || ratio === undefined) return null
  return ratio.toFixed(2)
}
