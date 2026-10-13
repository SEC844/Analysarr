import { useEffect, useState } from "react"
import { useLocation } from "react-router-dom"

// Multiple de toutes les colonnes possibles de la grille (2, 3, 4, 6, 8, 10) :
// aucune rangée n'est coupée en deux.
const STEP = 120
// Nombre de cartes affichées par entrée d'historique : un retour arrière
// retrouve la même hauteur de page, donc la même position de défilement.
const shown = new Map<string, number>()

/** Affiche une longue liste par tranches, la suivante juste avant d'arriver en
 * bas : une bibliothèque de plusieurs milliers de médias ne construit pas des
 * milliers de cartes d'un coup. */
export function useIncrementalList<T>(items: readonly T[]): {
  visible: readonly T[]
  sentinel: (element: HTMLElement | null) => void
} {
  const { key } = useLocation()
  // Nouvelle entrée d'historique (filtres changés) : on repart de la première
  // tranche, ou de celle laissée sur cette entrée lors d'un retour arrière.
  const [state, setState] = useState(() => ({ key, count: shown.get(key) ?? STEP }))
  if (state.key !== key) setState({ key, count: shown.get(key) ?? STEP })
  const count = state.key === key ? state.count : (shown.get(key) ?? STEP)
  const [element, setElement] = useState<HTMLElement | null>(null)
  const supported = typeof IntersectionObserver !== "undefined"
  const total = items.length

  useEffect(() => {
    shown.set(key, count)
  }, [key, count])

  useEffect(() => {
    if (!supported || !element || count >= total) return
    // Recréé à chaque tranche : un nouvel observateur signale tout de suite
    // une sentinelle encore visible (grand écran), l'ancien ne le referait pas.
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setState((current) => ({ ...current, count: current.count + STEP }))
        }
      },
      { rootMargin: "0px 0px 1500px 0px" },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [supported, element, count, total])

  return { visible: supported ? items.slice(0, count) : items, sentinel: setElement }
}
