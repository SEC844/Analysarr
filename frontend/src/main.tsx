import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import { BrowserRouter } from "react-router-dom"

import { Toaster } from "@/components/ui/sonner"
import { IS_DEV_BUILD } from "@/components/ui/logo"
import { AUTH_STATUS_QUERY_KEY } from "@/hooks/use-auth"
import { setSessionExpiredHandler } from "@/lib/api"
import { I18nProvider } from "@/i18n"
import "./index.css"
import App from "./App.tsx"

const queryClient = new QueryClient()

// Session perdue pendant l'utilisation (cookie expiré, révoqué, ou requête qui
// n'arrive plus authentifiée jusqu'au serveur) : on repasse à l'écran de
// connexion sans recharger la page. Recharger relançait les mêmes appels et
// donc le même 401, en boucle.
setSessionExpiredHandler(() => {
  const current = queryClient.getQueryData<{ authenticated: boolean }>(AUTH_STATUS_QUERY_KEY)
  if (current && !current.authenticated) return
  queryClient.setQueryData(AUTH_STATUS_QUERY_KEY, { setup_required: false, authenticated: false })
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== "auth" })
})

// Image de développement : même signal que le point rouge du logo, jusque dans
// l'onglet du navigateur (plusieurs instances ouvertes côte à côte).
if (IS_DEV_BUILD) {
  document.querySelectorAll<HTMLLinkElement>('link[rel="icon"], link[rel="apple-touch-icon"]').forEach((link) => {
    link.href = "/favicon-dev.svg"
  })
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
      <QueryClientProvider client={queryClient}>
        <I18nProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </I18nProvider>
        <Toaster />
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
