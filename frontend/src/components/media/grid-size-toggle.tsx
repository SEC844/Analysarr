import { Square } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type GridSize = "small" | "medium" | "large"

export const GRID_SIZE_CLASSES: Record<GridSize, string> = {
  small: "grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8",
  medium: "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6",
  large: "grid-cols-2 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4",
}

// Le pictogramme est un carré dessiné à la taille réelle qu'auront les
// cartes, plutôt qu'une icône de "densité de grille" dont la signification
// (plus de carrés = plus petit ou plus grand ?) n'est pas évidente au premier
// coup d'œil.
const OPTIONS: { value: GridSize; iconSize: string; label: string }[] = [
  { value: "large", iconSize: "size-4.5", label: "Grande" },
  { value: "medium", iconSize: "size-3.5", label: "Moyenne" },
  { value: "small", iconSize: "size-2.5", label: "Petite" },
]

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
            <Square className={option.iconSize} />
          </Button>
        )
      })}
    </div>
  )
}
