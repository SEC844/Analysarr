import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import type { ReactElement, ReactNode } from "react"

import { I18nProvider } from "@/i18n"

/** Client de requêtes de test : aucune nouvelle tentative, une erreur
 * d'API doit apparaître tout de suite. */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

/** Enveloppe des hooks et composants : requêtes et traductions. */
export function createWrapper(queryClient: QueryClient = createTestQueryClient()) {
  return function Providers({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider>{children}</I18nProvider>
      </QueryClientProvider>
    )
  }
}

export function renderWithProviders(ui: ReactElement, queryClient: QueryClient = createTestQueryClient()) {
  return { ...render(ui, { wrapper: createWrapper(queryClient) }), queryClient }
}
