import { useState } from "react"
import { Link, useSearchParams } from "react-router-dom"

import { GRID_SIZE_CLASSES, GridSizeToggle, type GridSize } from "@/components/media/grid-size-toggle"
import { MediaCard } from "@/components/media/media-card"
import { MediaFilters } from "@/components/media/media-filters"
import { ScanButton } from "@/components/media/scan-button"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { usePreferences } from "@/hooks/use-app"
import { useMediaListQuery } from "@/hooks/use-media"
import { useScrollRestoration } from "@/hooks/use-scroll-restoration"
import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"
import type { MediaListParams, MediaSort } from "@/types/media"

// Les filtres vivent dans l'URL (pas un simple useState) : ils survivent ainsi
// à un retour arrière depuis la fiche détail, et une bibliothèque filtrée
// reste partageable/bookmarkable. Le tri par défaut (Réglages → Préférences)
// n'apparaît pas dans l'URL.
function paramsToFilters(params: URLSearchParams, defaultSort: MediaSort): MediaListParams {
  return {
    status: (params.get("status") as MediaListParams["status"]) ?? undefined,
    media_type: (params.get("type") as MediaListParams["media_type"]) ?? undefined,
    watch: (params.get("watch") as MediaListParams["watch"]) ?? undefined,
    search: params.get("q") ?? undefined,
    sort: (params.get("sort") as MediaSort) ?? defaultSort,
  }
}

function filtersToParams(filters: MediaListParams, defaultSort: MediaSort): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.status) params.set("status", filters.status)
  if (filters.media_type) params.set("type", filters.media_type)
  if (filters.watch) params.set("watch", filters.watch)
  if (filters.search) params.set("q", filters.search)
  if (filters.sort && filters.sort !== defaultSort) params.set("sort", filters.sort)
  return params
}

export function MediaListPage() {
  const { t, rich } = useI18n()
  const prefs = usePreferences()
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = paramsToFilters(searchParams, prefs.library_default_sort)
  // Taille choisie pendant la visite ; à défaut, celle des préférences.
  const [gridOverride, setGridOverride] = useState<GridSize | null>(null)
  const gridSize = gridOverride ?? prefs.library_default_grid
  const { data, isLoading, isError } = useMediaListQuery(filters)

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
          <ScanButton />
          <GridSizeToggle value={gridSize} onChange={setGridOverride} />
        </div>
      </div>

      <div className="mb-6">
        <MediaFilters
          value={filters}
          defaultSort={prefs.library_default_sort}
          onChange={(next) => setSearchParams(filtersToParams(next, prefs.library_default_sort), { replace: true })}
        />
      </div>

      {isLoading && (
        <div className={cn("grid gap-4", GRID_SIZE_CLASSES[gridSize])}>
          {Array.from({ length: 12 }).map((_, i) => (
            <Skeleton key={i} className="aspect-2/3 w-full" />
          ))}
        </div>
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
        <div className={cn("grid gap-4", GRID_SIZE_CLASSES[gridSize])}>
          {data.items.map((media) => (
            <MediaCard key={media.id} media={media} />
          ))}
        </div>
      )}
    </div>
  )
}
