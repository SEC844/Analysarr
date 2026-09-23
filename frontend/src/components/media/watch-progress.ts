import type { useI18n } from "@/i18n"
import type { MediaWatchStats, WatchUser } from "@/types/media"

type Translate = ReturnType<typeof useI18n>["t"]

// Films : "45 %". Séries : "18/20" épisodes vus.
export function watchProgressLabel(user: WatchUser, stats: MediaWatchStats, t: Translate): string {
  return stats.total_episodes !== null
    ? t("watch.episodes", { watched: Math.round(user.progress), total: stats.total_episodes })
    : t("watch.percent", { value: Math.round(user.progress) })
}
