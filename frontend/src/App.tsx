import { Route, Routes } from "react-router-dom"

import { AppShell } from "@/components/layout/app-shell"
import { Skeleton } from "@/components/ui/skeleton"
import { useSettingsQuery } from "@/hooks/use-settings"
import { MediaDetailPage } from "@/pages/media-detail-page"
import { MediaListPage } from "@/pages/media-list-page"
import { OnboardingWizard } from "@/pages/onboarding-wizard"
import { SettingsPage } from "@/pages/settings-page"

function FullPageState({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-svh items-center justify-center px-4">{children}</div>
}

function App() {
  const { data, isLoading, isError } = useSettingsQuery()

  if (isLoading) {
    return (
      <FullPageState>
        <Skeleton className="h-8 w-64" />
      </FullPageState>
    )
  }

  if (isError || !data) {
    return (
      <FullPageState>
        <p className="text-destructive">
          Impossible de contacter l'API Analysarr. Vérifiez que le conteneur est bien démarré.
        </p>
      </FullPageState>
    )
  }

  if (!data.configured) {
    return <OnboardingWizard existing={data} />
  }

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<MediaListPage />} />
        <Route path="/media/:id" element={<MediaDetailPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </AppShell>
  )
}

export default App
