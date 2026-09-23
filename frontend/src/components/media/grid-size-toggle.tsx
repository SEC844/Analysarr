import type { GridSize } from "@/components/media/grid-size"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

// Un seul carré, une grille de 4, une grille de 9 — toutes dans le même
// encombrement : le pictogramme montre directement le résultat (nombre de
// cartes par rangée), pas une métaphore à interpréter.
const OPTIONS: { value: GridSize; cells: number }[] = [
  { value: "large", cells: 1 },
  { value: "medium", cells: 4 },
  { value: "small", cells: 9 },
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
  const { t } = useI18n()
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
            aria-label={t("grid.label", { size: t(`grid.${option.value}`) })}
            aria-pressed={active}
          >
            <GridSizeIcon cells={option.cells} />
          </Button>
        )
      })}
    </div>
  )
}
