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
