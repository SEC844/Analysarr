import type { I18nContextValue, MessageKey } from "@/i18n/core"
import { formatBytes, formatRelativeTime } from "@/lib/format"
import { obligationText } from "@/lib/seed"
import type { CleanupCandidate, Protection, ScoreComponent } from "@/types/cleanup"

type Translate = I18nContextValue["t"]

const SERIES_STATUSES = ["continuing", "ended", "upcoming", "deleted"] as const

function seriesStatusLabel(t: Translate, status: string | null): string {
  const known = SERIES_STATUSES.find((value) => value === status)
  return known ? t(`media.seriesStatus.${known}`) : (status ?? "")
}

/** Teinte du badge de score : plus il est haut, plus la suppression est
 * pertinente. */
export function scoreTone(score: number): "high" | "medium" | "low" {
  if (score >= 70) return "high"
  if (score >= 40) return "medium"
  return "low"
}

/** Raison principale d'une ligne, formulée pour un humain. */
export function mainReasonText(t: Translate, candidate: CleanupCandidate): string | null {
  switch (candidate.main_reason) {
    case "disinterest":
      if (candidate.last_played_at) {
        return t("cleanup.reasons.disinterest_played", { time: formatRelativeTime(candidate.last_played_at) ?? "" })
      }
      if (candidate.date_added) {
        return t("cleanup.reasons.disinterest_added", { time: formatRelativeTime(candidate.date_added) ?? "" })
      }
      return t("cleanup.reasons.disinterest_unknown")
    case "potential":
      return candidate.active_users === 0 ? t("cleanup.reasons.potential_nobody") : t("cleanup.reasons.potential")
    case "age":
      return t("cleanup.reasons.age", { time: formatRelativeTime(candidate.date_added) ?? "" })
    case "series":
      return t("cleanup.reasons.series", { status: seriesStatusLabel(t, candidate.series_status) })
    default:
      return null
  }
}

/** Raison d'une protection : pourquoi ce média n'est jamais proposé. */
export function protectionText(t: Translate, protection: Protection): string {
  const names = protection.names.join(", ")
  switch (protection.kind) {
    case "favorite":
      return t("cleanup.protections.favorite", { names })
    case "request":
      return t("cleanup.protections.request", { names })
    case "request_unknown":
      return t("cleanup.protections.request_unknown")
    case "recent":
      return t("cleanup.protections.recent", { count: protection.days ?? 0 })
    case "seed":
      return protection.obligation ? obligationText(t, protection.obligation) : t("seed.badge")
    case "excluded":
      return t("cleanup.protections.excluded")
  }
}

/** Ce qui a produit la valeur d'un sous-score. */
export function componentDetail(t: Translate, component: ScoreComponent): string {
  switch (component.key) {
    case "disinterest":
      if (component.since === "undated") return t("cleanup.breakdown.disinterestUndated")
      if (component.days === null) return t("cleanup.breakdown.unknownDate")
      return component.since === "last_played"
        ? t("cleanup.breakdown.disinterestPlayed", { count: component.days })
        : t("cleanup.breakdown.disinterestAdded", { count: component.days })
    case "potential":
      return component.users
        ? t("cleanup.breakdown.potential", { unfinished: component.unfinished ?? 0, users: component.users })
        : t("cleanup.breakdown.potentialNobody")
    case "age":
      return component.days === null
        ? t("cleanup.breakdown.unknownDate")
        : t("cleanup.breakdown.age", { count: component.days })
    case "series":
      return seriesStatusLabel(t, component.series_status)
  }
}

export function componentName(key: ScoreComponent["key"]): MessageKey {
  return `cleanup.breakdown.components.${key}`
}

/** Total de la sélection : somme des espaces libérables (exacte sauf fichier
 * hardlinké entre deux médias sélectionnés). */
export function selectionSize(selected: Iterable<{ reclaimable_bytes: number }>): string {
  let total = 0
  for (const item of selected) total += item.reclaimable_bytes
  return formatBytes(total)
}
