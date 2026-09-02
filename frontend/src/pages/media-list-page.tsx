import { useState } from "react"
import { Link } from "react-router-dom"

import { GRID_SIZE_CLASSES, GridSizeToggle, type GridSize } from "@/components/media/grid-size-toggle"
import { MediaCard } from "@/components/media/media-card"
import { MediaFilters } from "@/components/media/media-filters"
import { ScanButton } from "@/components/media/scan-button"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useMediaListQuery } from "@/hooks/use-media"
import { cn } from "@/lib/utils"
import type { MediaListParams } from "@/types/media"

const GRID_SIZE_STORAGE_KEY = "analysarr:grid-size"

function loadGridSize(): GridSize {
  try {
    const stored = localStorage.getItem(GRID_SIZE_STORAGE_KEY)
    if (stored === "small" || stored === "medium" || stored === "large") return stored
  } catch {
    // localStorage indisponible (navigation privée, etc.) : on garde la valeur par défaut
  }
  return "medium"
}

export function MediaListPage() {
  const [filters, setFilters] = useState<MediaListParams>({ sort: "title" })
  const [gridSize, setGridSize] = useState<GridSize>(loadGridSize)
  const { data, isLoading, isError } = useMediaListQuery(filters)

  const handleGridSizeChange = (size: GridSize) => {
    setGridSize(size)
    try {
      localStorage.setItem(GRID_SIZE_STORAGE_KEY, size)
    } catch {
      // pas grave si la préférence ne peut pas être mémorisée
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-8">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Bibliothèque</h1>
          <p className="text-muted-foreground text-sm">
            {data ? `${data.total} média${data.total > 1 ? "s" : ""}` : "Chargement..."}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <ScanButton />
          <GridSizeToggle value={gridSize} onChange={handleGridSizeChange} />
        </div>
      </div>

      <div className="mb-6">
        <MediaFilters value={filters} onChange={setFilters} />
      </div>

      {isLoading && (
        <div className={cn("grid gap-4", GRID_SIZE_CLASSES[gridSize])}>
          {Array.from({ length: 12 }).map((_, i) => (
            <Skeleton key={i} className="aspect-2/3 w-full" />
          ))}
        </div>
      )}

      {isError && <p className="text-destructive">Impossible de charger la bibliothèque.</p>}

      {data && data.items.length === 0 && (
        <div className="text-muted-foreground flex flex-col items-center gap-3 py-16 text-center">
          <p>Aucun média trouvé.</p>
          <p className="text-sm">
            Lancez un premier scan avec le bouton ci-dessus, ou vérifiez vos{" "}
            <Button variant="link" className="h-auto p-0" render={<Link to="/settings" />}>
              réglages
            </Button>
            .
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
