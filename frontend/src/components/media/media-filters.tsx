import { Search, X } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useI18n, type MessageKey } from "@/i18n"
import type { MediaListParams } from "@/types/media"

interface MediaFiltersProps {
  value: MediaListParams
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
]
const SORT_OPTIONS: [string, MessageKey][] = [
  ["title", "filters.sortTitle"],
  ["year", "filters.sortYear"],
  ["size", "filters.sortSize"],
]

export function MediaFilters({ value, onChange }: MediaFiltersProps) {
  const { t } = useI18n()
  const hasActiveFilters = Boolean(value.status || value.media_type || value.search || (value.sort && value.sort !== "title"))
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

      <Select value={value.sort ?? "title"} onValueChange={(v) => onChange({ ...value, sort: v as MediaListParams["sort"] })}>
        <SelectTrigger className="w-44">
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
        <Button type="button" variant="ghost" size="sm" onClick={() => onChange({ sort: "title" })}>
          <X className="size-4" />
          {t("filters.reset")}
        </Button>
      )}
    </div>
  )
}
