// Matchers du DOM (toBeInTheDocument, toBeDisabled…) et démontage après
// chaque test : les globals de Vitest ne sont pas activés, Testing Library ne
// nettoie donc pas tout seul.
import "@testing-library/jest-dom/vitest"
import { cleanup } from "@testing-library/react"
import { afterEach } from "vitest"

import { clearSessionState } from "@/hooks/use-session-state"

// Mémoire de visite (sélection, taille des cartes...) : chaque test repart à zéro.
afterEach(() => {
  cleanup()
  clearSessionState()
})
