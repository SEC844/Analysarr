import type { I18nContextValue } from "@/i18n/core"
import { formatDate, formatRatio } from "@/lib/format"
import type { SeedObligation } from "@/types/seed"

/** « Protégé jusqu'au 12/10/2026 (seed minimum sur tracker.exemple.org) » :
 * même phrase dans l'aperçu du nettoyage, sur la ligne d'un torrent et dans
 * l'avertissement de la suppression manuelle. */
export function obligationText(t: I18nContextValue["t"], obligation: SeedObligation): string {
  if (obligation.reason === "unknown_date") return t("seed.obligation.unknown_date")
  if (obligation.reason === "min_ratio") {
    return t("seed.obligation.min_ratio", {
      ratio: formatRatio(obligation.min_ratio) ?? "",
      tracker: obligation.tracker ?? "",
    })
  }
  const date = formatDate(obligation.until) ?? ""
  return obligation.tracker
    ? t("seed.obligation.min_seed", { date, tracker: obligation.tracker })
    : t("seed.obligation.min_seed_general", { date })
}
