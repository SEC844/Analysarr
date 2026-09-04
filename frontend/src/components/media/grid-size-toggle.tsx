import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type GridSize = "small" | "medium" | "large"

// Chaque palier décalé d'un cran vers plus de colonnes (cartes plus petites) :
// même la plus grande taille précédente restait trop imposante.
export const GRID_SIZE_CLASSES: Record<GridSize, string> = {
  small: "grid-cols-4 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10",
  medium: "grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8",
  large: "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6",
}

// Un seul carré, une grille de 4, une grille de 9 — toutes dans le même
// encombrement : le pictogramme montre directement le résultat (nombre de
// cartes par rangée), pas une métaphore à interpréter.
const OPTIONS: { value: GridSize; cells: number; label: string }[] = [
  { value: "large", cells: 1, label: "Grande" },
  { value: "medium", cells: 4, label: "Moyenne" },
  { value: "small", cells: 9, label: "Petite" },
]

function GridSizeIcon({ cells }: { cells: number }) {
  const cols = Math.sqrt(cells)
  return (
    <span
      className="grid size-3.5 gap-0.5"
      style={{ gridTemplateColumns: `repeat(${cols}, 1fr)`, gridTemplateRows: `repeat(${cols}, 1fr)` }}
    >
      {Array.from({ length: cells }).map((_, i) => (
        <span key={i} className="rounded-[1px] bg-current" />
      ))}
    </span>
  )
}

export function GridSizeToggle({ value, onChange }: { value: GridSize; onChange: (size: GridSize) => void }) {
  return (
    <div className="border-border flex items-center gap-0.5 rounded-lg border p-0.5">
      {OPTIONS.map((option) => {
        const active = value === option.value
        return (
          <Button
            key={option.value}
            type="button"
            variant={active ? "secondary" : "ghost"}
            size="icon-sm"
            className={cn(!active && "text-muted-foreground")}
            onClick={() => onChange(option.value)}
            aria-label={`Taille des cartes : ${option.label}`}
            aria-pressed={active}
          >
            <GridSizeIcon cells={option.cells} />
          </Button>
        )
      })}
    </div>
  )
}
