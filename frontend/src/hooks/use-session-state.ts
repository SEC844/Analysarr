import { useEffect, useState, type Dispatch, type SetStateAction } from "react"
import { useLocation } from "react-router-dom"

// Mémoire de la visite (perdue au rechargement, comme la position de défilement
// de use-scroll-restoration) : ce que l'utilisateur a choisi sur une page la
// retrouve quand il y revient, quel que soit le chemin (lien de l'en-tête,
// retour arrière, fiche d'un média).
const memory = new Map<string, unknown>()

/** `useState` dont la valeur survit au démontage de la page. Une clé ne doit
 * servir qu'à un seul type de valeur. */
export function useSessionState<T>(key: string, initial: T): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => (memory.has(key) ? (memory.get(key) as T) : initial))
  useEffect(() => {
    memory.set(key, value)
  }, [key, value])
  return [value, setValue]
}

/** Tests : chaque cas repart d'une visite neuve. */
export function clearSessionState(): void {
  memory.clear()
}

/** Adresse d'une page principale telle que l'utilisateur l'a laissée (filtres
 * compris) : le lien de l'en-tête y ramène au lieu d'une page remise à zéro. */
export function useRememberedPath(): (path: string) => string {
  const { pathname, search } = useLocation()
  useEffect(() => {
    memory.set(`location:${pathname}`, search)
  }, [pathname, search])
  return (path) => {
    if (path === pathname) return path + search
    const saved = memory.get(`location:${path}`)
    return typeof saved === "string" ? path + saved : path
  }
}
