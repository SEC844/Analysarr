import { useMemo } from "react"
import { Link, useSearchParams } from "react-router-dom"

import { AutomationsPausedBanner } from "@/components/automations/automations-paused-banner"
import { RealtimeIndicator } from "@/components/realtime/realtime-indicator"
import { SeedProtectionBanner } from "@/components/seed/seed-protection-banner"
import { GridSizeToggle } from "@/components/media/grid-size-toggle"
import { MediaGrid } from "@/components/media/media-grid"
import { MediaCard, type MediaCardDisplay } from "@/components/media/media-card"
import { MediaFilters } from "@/components/media/media-filters"
import { ScanButton } from "@/components/media/scan-button"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { usePreferences } from "@/hooks/use-app"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { useGridSize } from "@/hooks/use-grid-size"
import { useIncrementalList } from "@/hooks/use-incremental-list"
import { useMediaListQuery } from "@/hooks/use-media"
import { useScrollRestoration } from "@/hooks/use-scroll-restoration"
import { useI18n } from "@/i18n"
import type { MediaListItem, MediaListParams, MediaSort, MediaStatus, WatchFilter } from "@/types/media"

// Les filtres vivent dans l'URL (pas un simple useState) : ils survivent ainsi
// à un retour arrière depuis la fiche détail, et une bibliothèque filtrée
// reste partageable/bookmarkable. Le tri par défaut (Réglages → Préférences)
// n'apparaît pas dans l'URL.
const SEARCH_DELAY_MS = 250
const NO_ITEMS: MediaListItem[] = []

function listOf<T extends string>(params: URLSearchParams, key: string): T[] | undefined {
  const values = (params.get(key) ?? "").split(",").filter(Boolean) as T[]
  return values.length > 0 ? values : undefined
}

function paramsToFilters(params: URLSearchParams, defaultSort: MediaSort): MediaListParams {
  return {
    status: listOf<MediaStatus>(params, "status"),
    match: params.get("match") === "all" ? "all" : undefined,
    health: (params.get("health") as MediaListParams["health"]) ?? undefined,
    media_type: (params.get("type") as MediaListParams["media_type"]) ?? undefined,
    watch: listOf<WatchFilter>(params, "watch"),
    search: params.get("q") ?? undefined,
    sort: (params.get("sort") as MediaSort) ?? defaultSort,
  }
}

function filtersToParams(filters: MediaListParams, defaultSort: MediaSort): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.status?.length) params.set("status", filters.status.join(","))
  if (filters.match) params.set("match", filters.match)
  if (filters.health) params.set("health", filters.health)
  if (filters.media_type) params.set("type", filters.media_type)
  if (filters.watch?.length) params.set("watch", filters.watch.join(","))
  if (filters.search) params.set("q", filters.search)
  if (filters.sort && filters.sort !== defaultSort) params.set("sort", filters.sort)
  return params
}

export function MediaListPage() {
  const { t, rich } = useI18n()
  const prefs = usePreferences()
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = paramsToFilters(searchParams, prefs.library_default_sort)
  // Le champ de recherche suit la frappe ; la requête attend une pause.
  const search = useDebouncedValue(filters.search, SEARCH_DELAY_MS)
  const [gridSize, setGridSize] = useGridSize()
  const { data, isLoading, isError } = useMediaListQuery({ ...filters, search })
  const { visible, sentinel } = useIncrementalList(data?.items ?? NO_ITEMS)
  const display = useMemo<MediaCardDisplay>(
    () => ({
      watch: prefs.card_show_watch,
      totalSize: prefs.card_show_total_size,
      requestedBy: prefs.card_show_requested_by,
      reclaimable: prefs.card_show_reclaimable,
    }),
    [prefs.card_show_watch, prefs.card_show_total_size, prefs.card_show_requested_by, prefs.card_show_reclaimable],
  )

  useScrollRestoration(!isLoading)

  return (
    <div className="mx-auto max-w-[1600px] px-4 py-8 sm:px-6">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("library.title")}</h1>
          <p className="text-muted-foreground text-sm">
            {data ? t("library.count", { count: data.total }) : t("common.loading")}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <RealtimeIndicator />
          <ScanButton />
          <GridSizeToggle value={gridSize} onChange={setGridSize} />
        </div>
      </div>

      {/* Automatisations suspendues par le garde-fou : visible dès l'accueil,
          sinon la pause passerait inaperçue jusqu'à l'ouverture des réglages.
          Même endroit pour la protection du seed proposée aux installations
          existantes. */}
      <div className="mb-6 space-y-3 empty:mb-0">
        <AutomationsPausedBanner />
        <SeedProtectionBanner />
      </div>

      <div className="mb-6">
        <MediaFilters
          value={filters}
          defaultSort={prefs.library_default_sort}
          onChange={(next) => setSearchParams(filtersToParams(next, prefs.library_default_sort), { replace: true })}
        />
      </div>

      {isLoading && (
        <MediaGrid size={gridSize}>
          {Array.from({ length: 12 }).map((_, i) => (
            <Skeleton key={i} className="aspect-2/3 w-full" />
          ))}
        </MediaGrid>
      )}

      {isError && <p className="text-destructive">{t("library.loadFailed")}</p>}

      {data && data.items.length === 0 && (
        <div className="text-muted-foreground flex flex-col items-center gap-3 py-16 text-center">
          <p>{t("library.empty")}</p>
          <p className="text-sm">
            {rich("library.emptyHint", {
              link: (
                <Button variant="link" className="h-auto p-0" render={<Link to="/settings" />}>
                  {t("library.settingsLink")}
                </Button>
              ),
            })}
          </p>
        </div>
      )}

      {data && data.items.length > 0 && (
        <>
          <MediaGrid size={gridSize}>
            {visible.map((media) => (
              <MediaCard key={media.id} media={media} display={display} />
            ))}
          </MediaGrid>
          <div ref={sentinel} aria-hidden="true" />
        </>
      )}
    </div>
  )
}
