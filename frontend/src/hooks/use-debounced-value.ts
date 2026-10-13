import { useEffect, useState } from "react"

/** Valeur recopiée après `delay` ms sans changement : une recherche tapée au
 * clavier part en une requête, pas une par lettre. */
export function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}
