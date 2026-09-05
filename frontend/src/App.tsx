import { Route, Routes } from "react-router-dom"

import { AppShell } from "@/components/layout/app-shell"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuthStatusQuery } from "@/hooks/use-auth"
import { useSettingsQuery } from "@/hooks/use-settings"
import { LoginPage } from "@/pages/login-page"
import { MediaDetailPage } from "@/pages/media-detail-page"
import { MediaListPage } from "@/pages/media-list-page"
import { OnboardingWizard } from "@/pages/onboarding-wizard"
import { SettingsPage } from "@/pages/settings-page"
import { SetupAdminPage } from "@/pages/setup-admin-page"

function FullPageState({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-svh items-center justify-center px-4">{children}</div>
}

function App() {
  const authStatus = useAuthStatusQuery()
  // Ne part chercher /api/settings (protégé) qu'une fois l'authentification
  // confirmée — sinon un 401 pendant l'écran de connexion déclenche la
  // redirection sur 401 générique de request() (lib/api.ts) et boucle.
  const settings = useSettingsQuery(authStatus.data?.authenticated ?? false)

  if (authStatus.isLoading) {
    return (
      <FullPageState>
        <Skeleton className="h-8 w-64" />
      </FullPageState>
    )
  }

  if (authStatus.isError || !authStatus.data) {
    return (
      <FullPageState>
        <p className="text-destructive">
          Impossible de contacter l'API Analysarr. Vérifiez que le conteneur est bien démarré.
        </p>
      </FullPageState>
    )
  }

  if (authStatus.data.setup_required) {
    return <SetupAdminPage />
  }

  if (!authStatus.data.authenticated) {
    return <LoginPage />
  }

  if (settings.isPending) {
    // isPending (pas isLoading) : englobe aussi le rendu juste après le
    // passage de `enabled` à true, avant que la requête n'ait démarré —
    // sinon un instant sans donnée ni fetch en cours tombe à tort dans le
    // cas d'erreur ci-dessous.
    return (
      <FullPageState>
        <Skeleton className="h-8 w-64" />
      </FullPageState>
    )
  }

  if (settings.isError || !settings.data) {
    return (
      <FullPageState>
        <p className="text-destructive">
          Impossible de contacter l'API Analysarr. Vérifiez que le conteneur est bien démarré.
        </p>
      </FullPageState>
    )
  }

  if (!settings.data.configured) {
    return <OnboardingWizard existing={settings.data} />
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
