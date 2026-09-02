import { useEffect } from "react"
import { useLocation } from "react-router-dom"

// Mémoire de session simple (perdue au rechargement complet, ce qui est très
// bien : on ne veut restaurer le scroll qu'en revenant en arrière dans la
// même session de navigation SPA, pas après un F5).
const scrollPositions = new Map<string, number>()

/**
 * Restaure la position de scroll sauvegardée pour cette entrée d'historique
 * une fois que `ready` passe à true (typiquement quand les données sont
 * chargées et que la page a atteint sa hauteur finale). Fonctionne avec un
 * retour arrière (navigate(-1) / bouton précédent du navigateur), qui réutilise
 * la même clé d'entrée d'historique — pas avec une navigation <Link> qui en
 * crée une nouvelle.
 */
export function useScrollRestoration(ready: boolean) {
  const location = useLocation()

  useEffect(() => {
    if (!ready) return
    const saved = scrollPositions.get(location.key)
    if (saved !== undefined) {
      window.scrollTo(0, saved)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, location.key])

  useEffect(() => {
    const handleScroll = () => {
      scrollPositions.set(location.key, window.scrollY)
    }
    window.addEventListener("scroll", handleScroll, { passive: true })
    return () => window.removeEventListener("scroll", handleScroll)
  }, [location.key])
}
