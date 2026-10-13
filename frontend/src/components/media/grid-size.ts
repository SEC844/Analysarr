export type GridSize = "small" | "medium" | "large"

// Chaque palier décalé d'un cran vers plus de colonnes (cartes plus petites) :
// même la plus grande taille précédente restait trop imposante. Colonnes
// selon la largeur de la GRILLE (requêtes de conteneur), pas de l'écran : les
// cartes gardent la même taille dans la bibliothèque, pleine largeur, et dans
// le nettoyage, à côté de son menu.
export const GRID_SIZE_CLASSES: Record<GridSize, string> = {
  small: "grid-cols-4 @xl:grid-cols-6 @2xl:grid-cols-8 @4xl:grid-cols-10",
  medium: "grid-cols-3 @xl:grid-cols-4 @2xl:grid-cols-6 @4xl:grid-cols-8",
  large: "grid-cols-2 @xl:grid-cols-3 @2xl:grid-cols-4 @4xl:grid-cols-6",
}
