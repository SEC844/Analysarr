import { useState } from "react"
import { Link } from "react-router-dom"

import { MediaCard } from "@/components/media/media-card"
import { MediaFilters } from "@/components/media/media-filters"
import { ScanButton } from "@/components/media/scan-button"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useMediaListQuery } from "@/hooks/use-media"
import type { MediaListParams } from "@/types/media"

export function MediaListPage() {
  const [filters, setFilters] = useState<MediaListParams>({ sort: "title" })
  const { data, isLoading, isError } = useMediaListQuery(filters)

  return (
    <div className="mx-auto max-w-6xl px-4 py-8">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Bibliothèque</h1>
          <p className="text-muted-foreground text-sm">
            {data ? `${data.total} média${data.total > 1 ? "s" : ""}` : "Chargement..."}
          </p>
        </div>
        <ScanButton />
      </div>

      <div className="mb-6">
        <MediaFilters value={filters} onChange={setFilters} />
      </div>

      {isLoading && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
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
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
          {data.items.map((media) => (
            <MediaCard key={media.id} media={media} />
          ))}
        </div>
      )}
    </div>
  )
}
