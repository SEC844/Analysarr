import type { GridSize } from "@/components/media/grid-size"
import { usePreferences } from "@/hooks/use-app"
import { useSessionState } from "@/hooks/use-session-state"

/** Taille des cartes, commune à la bibliothèque et au nettoyage : celle
 * choisie pendant la visite, à défaut celle des préférences. */
export function useGridSize(): [GridSize, (size: GridSize) => void] {
  const prefs = usePreferences()
  const [chosen, setChosen] = useSessionState<GridSize | null>("grid-size", null)
  return [chosen ?? prefs.library_default_grid, setChosen]
}
