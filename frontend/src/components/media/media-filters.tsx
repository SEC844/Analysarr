import { Filter, Search, X } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { useI18n, type MessageKey } from "@/i18n"
import { ALERT_STATUSES, type MediaListParams, type MediaSort, type MediaStatus, type WatchFilter } from "@/types/media"

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
// État : la lecture la plus utile au quotidien — ce qui va bien, ce qui demande
// une action. Le détail par statut vit dans le panneau « Filtres ».
const HEALTH_OPTIONS: [string, MessageKey][] = [
  ["all", "filters.allHealth"],
  ["sain", "filters.healthy"],
  ["alerte", "filters.alert"],
  ["masque", "filters.muted"],
]
const WATCH_OPTIONS: [WatchFilter, MessageKey][] = [
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

function toggle<T extends string>(list: T[] | undefined, item: T, checked: boolean): T[] | undefined {
  const next = checked ? [...(list ?? []), item] : (list ?? []).filter((value) => value !== item)
  return next.length > 0 ? next : undefined
}

/** Statuts et visionnage en cases à cocher : un média peut en porter
 * plusieurs, les croiser est tout l'intérêt (issue #35). */
function DetailedFilters({ value, onChange }: { value: MediaListParams; onChange: (value: MediaListParams) => void }) {
  const { t } = useI18n()
  const statuses = value.status ?? []
  const watches = value.watch ?? []

  return (
    <div className="min-w-0 space-y-4">
      <div className="space-y-2">
        <p className="text-sm font-medium">{t("filters.status")}</p>
        <div className="grid gap-1.5 sm:grid-cols-2">
          {ALERT_STATUSES.map((status) => (
            <label key={status} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={statuses.includes(status)}
                onCheckedChange={(checked) => onChange({ ...value, status: toggle(statuses, status, checked === true) })}
              />
              <span className="truncate">{t(`status.${status}` as MessageKey)}</span>
            </label>
          ))}
        </div>
        <div className="flex items-center justify-between gap-4 pt-1">
          <Label htmlFor="filters-match" className="font-normal">
            {t("filters.matchAll")}
          </Label>
          <Switch
            id="filters-match"
            checked={value.match === "all"}
            onCheckedChange={(checked) => onChange({ ...value, match: checked ? "all" : undefined })}
          />
        </div>
        <p className="text-muted-foreground text-xs">{t("filters.matchAllHelp")}</p>
      </div>

      <div className="space-y-2 border-t pt-3">
        <p className="text-sm font-medium">{t("watch.filter")}</p>
        <div className="grid gap-1.5 sm:grid-cols-2">
          {WATCH_OPTIONS.map(([option, key]) => (
            <label key={option} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={watches.includes(option)}
                onCheckedChange={(checked) => onChange({ ...value, watch: toggle(watches, option, checked === true) })}
              />
              <span className="truncate">{t(key)}</span>
            </label>
          ))}
        </div>
      </div>
    </div>
  )
}

export function MediaFilters({ value, defaultSort, onChange }: MediaFiltersProps) {
  const { t } = useI18n()
  const detailedCount = (value.status?.length ?? 0) + (value.watch?.length ?? 0)
  const hasActiveFilters = Boolean(
    detailedCount > 0 ||
      value.health ||
      value.media_type ||
      value.search ||
      (value.sort && value.sort !== defaultSort),
  )
  const labelOf = (options: [string, MessageKey][]) => (v: string) => {
    const key = options.find(([option]) => option === v)?.[1]
    return key ? t(key) : v
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative">
        <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
        <Input
          placeholder={t("filters.searchPlaceholder")}
          value={value.search ?? ""}
          onChange={(e) => onChange({ ...value, search: e.target.value || undefined })}
          className={value.search ? "w-56 pr-8 pl-8" : "w-56 pl-8"}
        />
        {value.search && (
          <button
            type="button"
            onClick={() => onChange({ ...value, search: undefined })}
            aria-label={t("filters.clearSearch")}
            className="text-muted-foreground hover:text-foreground absolute top-1/2 right-2 -translate-y-1/2"
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
        value={value.health ?? "all"}
        onValueChange={(v) => onChange({ ...value, health: v === "all" ? undefined : (v as NonNullable<MediaListParams["health"]>) })}
      >
        <SelectTrigger className="w-40">
          <SelectValue placeholder={t("filters.health")}>{labelOf(HEALTH_OPTIONS)}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {HEALTH_OPTIONS.map(([option, key]) => (
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

      <Popover>
        <PopoverTrigger render={<Button type="button" variant="outline" />}>
          <Filter className="size-4" />
          {t("filters.more")}
          {detailedCount > 0 && <Badge variant="secondary">{detailedCount}</Badge>}
        </PopoverTrigger>
        <PopoverContent className="w-80 max-w-[calc(100vw-2rem)]">
          <DetailedFilters value={value} onChange={onChange} />
        </PopoverContent>
      </Popover>

      {hasActiveFilters && (
        <Button type="button" variant="ghost" size="sm" onClick={() => onChange({ sort: defaultSort })}>
          <X className="size-4" />
          {t("filters.reset")}
        </Button>
      )}
    </div>
  )
}

export type { MediaStatus }
