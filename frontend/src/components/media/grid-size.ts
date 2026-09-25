export type GridSize = "small" | "medium" | "large"

// Chaque palier décalé d'un cran vers plus de colonnes (cartes plus petites) :
// même la plus grande taille précédente restait trop imposante.
export const GRID_SIZE_CLASSES: Record<GridSize, string> = {
  small: "grid-cols-4 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10",
  medium: "grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8",
  large: "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6",
}
