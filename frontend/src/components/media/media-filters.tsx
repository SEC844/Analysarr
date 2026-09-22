import { Search, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useI18n, type MessageKey } from "@/i18n"
import type { MediaListParams, MediaSort } from "@/types/media"

interface MediaFiltersProps {
  value: MediaListParams
  // Tri par défaut (Réglages → Préférences) : ni un filtre actif, ni perdu à la réinitialisation.
  defaultSort: MediaSort
  onChange: (value: MediaListParams) => void
}

const TYPE_OPTIONS: [string, MessageKey][] = [
  ["all", "filters.allTypes"],
  ["movie", "filters.movies"],
  ["series", "filters.series"],
]
const STATUS_OPTIONS: [string, MessageKey][] = [
  ["all", "filters.allStatuses"],
  ["sain", "status.sain"],
  ["doublon", "status.doublon"],
  ["orphelin_qbit", "status.orphelin_qbit"],
  ["non_hardlink", "status.non_hardlink"],
  ["tracker_unique", "status.tracker_unique"],
  ["manquant_emby", "status.manquant_emby"],
  ["manquant_qbit", "status.manquant_qbit"],
  ["manquant_arr", "status.manquant_arr"],
  ["import_rate", "status.import_rate"],
  ["telechargement_bloque", "status.telechargement_bloque"],
]
// "any" (et non "all", déjà une valeur de filtre : "vu par tous").
const WATCH_OPTIONS: [string, MessageKey][] = [
  ["any", "watch.filterAny"],
  ["never", "watch.filterNever"],
  ["in_progress", "watch.filterInProgress"],
  ["all", "watch.filterAllWatched"],
]
const SORT_OPTIONS: [string, MessageKey][] = [
  ["title", "filters.sortTitle"],
  ["year", "filters.sortYear"],
  ["size", "filters.sortSize"],
  ["last_played", "watch.sortLastPlayed"],
  ["cleanup", "watch.sortCleanup"],
]

export function MediaFilters({ value, defaultSort, onChange }: MediaFiltersProps) {
  const { t } = useI18n()
  const hasActiveFilters = Boolean(
    value.status || value.media_type || value.watch || value.search || (value.sort && value.sort !== defaultSort),
  )
  const labelOf = (options: [string, MessageKey][]) => (v: string) => {
    const key = options.find(([option]) => option === v)?.[1]
    return key ? t(key) : v
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative">
        <Search className="text-muted-foreground pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2" />
        <Input
          placeholder={t("filters.searchPlaceholder")}
          value={value.search ?? ""}
          onChange={(e) => onChange({ ...value, search: e.target.value || undefined })}
          className={value.search ? "w-56 pl-8 pr-8" : "w-56 pl-8"}
        />
        {value.search && (
          <button
            type="button"
            onClick={() => onChange({ ...value, search: undefined })}
            aria-label={t("filters.clearSearch")}
            className="text-muted-foreground hover:text-foreground absolute right-2 top-1/2 -translate-y-1/2"
          >
            <X className="size-4" />
          </button>
        )}
      </div>

      <Select
        value={value.media_type ?? "all"}
        onValueChange={(v) => onChange({ ...value, media_type: v === "all" ? undefined : (v as "movie" | "series") })}
      >
        <SelectTrigger className="w-36">
          <SelectValue placeholder={t("filters.type")}>{labelOf(TYPE_OPTIONS)}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {TYPE_OPTIONS.map(([option, key]) => (
            <SelectItem key={option} value={option}>
              {t(key)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={value.status ?? "all"}
        onValueChange={(v) => onChange({ ...value, status: v === "all" ? undefined : (v as MediaListParams["status"]) })}
      >
        <SelectTrigger className="w-44">
          <SelectValue placeholder={t("filters.status")}>{labelOf(STATUS_OPTIONS)}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {STATUS_OPTIONS.map(([option, key]) => (
            <SelectItem key={option} value={option}>
              {t(key)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={value.watch ?? "any"}
        onValueChange={(v) => onChange({ ...value, watch: v === "any" ? undefined : (v as MediaListParams["watch"]) })}
      >
        <SelectTrigger className="w-44">
          <SelectValue placeholder={t("watch.filter")}>{labelOf(WATCH_OPTIONS)}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {WATCH_OPTIONS.map(([option, key]) => (
            <SelectItem key={option} value={option}>
              {t(key)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={value.sort ?? defaultSort} onValueChange={(v) => onChange({ ...value, sort: v as MediaSort })}>
        <SelectTrigger className="w-52">
          <SelectValue placeholder={t("filters.sortBy")}>{labelOf(SORT_OPTIONS)}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {SORT_OPTIONS.map(([option, key]) => (
            <SelectItem key={option} value={option}>
              {t(key)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {hasActiveFilters && (
        <Button type="button" variant="ghost" size="sm" onClick={() => onChange({ sort: defaultSort })}>
          <X className="size-4" />
          {t("filters.reset")}
        </Button>
      )}
    </div>
  )
}
