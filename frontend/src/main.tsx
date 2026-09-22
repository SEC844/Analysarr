import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import { BrowserRouter } from "react-router-dom"

import { Toaster } from "@/components/ui/sonner"
import { IS_DEV_BUILD } from "@/components/ui/logo"
import { I18nProvider } from "@/i18n"
import "./index.css"
import App from "./App.tsx"

const queryClient = new QueryClient()

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
