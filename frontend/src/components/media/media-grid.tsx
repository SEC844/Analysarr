import type { ReactNode } from "react"

import { GRID_SIZE_CLASSES, type GridSize } from "@/components/media/grid-size"
import { cn } from "@/lib/utils"

/** Grille des cartes (bibliothèque, nettoyage, squelettes de chargement). */
export function MediaGrid({ size, children }: { size: GridSize; children: ReactNode }) {
  return (
    <div className="@container">
      <div className={cn("grid gap-4", GRID_SIZE_CLASSES[size])}>{children}</div>
    </div>
  )
}
