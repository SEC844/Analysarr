import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import { BrowserRouter } from "react-router-dom"

import { Toaster } from "@/components/ui/sonner"
import { I18nProvider } from "@/i18n"
import "./index.css"
import App from "./App.tsx"

const queryClient = new QueryClient()

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
