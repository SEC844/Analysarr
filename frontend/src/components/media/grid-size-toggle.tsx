import { Grid2x2, Grid3x3, LayoutGrid } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type GridSize = "small" | "medium" | "large"

export const GRID_SIZE_CLASSES: Record<GridSize, string> = {
  small: "grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8",
  medium: "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6",
  large: "grid-cols-2 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4",
}

const OPTIONS: { value: GridSize; icon: typeof Grid2x2; label: string }[] = [
  { value: "large", icon: Grid2x2, label: "Grande" },
  { value: "medium", icon: Grid3x3, label: "Moyenne" },
  { value: "small", icon: LayoutGrid, label: "Petite" },
]

export function GridSizeToggle({ value, onChange }: { value: GridSize; onChange: (size: GridSize) => void }) {
  return (
    <div className="border-border flex items-center gap-0.5 rounded-lg border p-0.5">
      {OPTIONS.map((option) => {
        const Icon = option.icon
        const active = value === option.value
        return (
          <Button
            key={option.value}
            type="button"
            variant={active ? "secondary" : "ghost"}
            size="icon-sm"
            className={cn(!active && "text-muted-foreground")}
            onClick={() => onChange(option.value)}
            aria-label={`Taille de grille : ${option.label}`}
            aria-pressed={active}
          >
            <Icon className="size-4" />
          </Button>
        )
      })}
    </div>
  )
}
