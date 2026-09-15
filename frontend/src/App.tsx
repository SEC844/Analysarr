import { useEffect, useRef } from "react"
import { Route, Routes } from "react-router-dom"
import { toast } from "sonner"

import { AppShell } from "@/components/layout/app-shell"
import { Skeleton } from "@/components/ui/skeleton"
import { useAppInfoQuery } from "@/hooks/use-app"
import { useAuthStatusQuery } from "@/hooks/use-auth"
import { useSettingsQuery } from "@/hooks/use-settings"
import { useI18n } from "@/i18n"
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
  const { t, language, setLanguage } = useI18n()
  const authStatus = useAuthStatusQuery()
  const authenticated = authStatus.data?.authenticated ?? false
  // Ne part chercher /api/settings (protégé) qu'une fois l'authentification
  // confirmée — sinon un 401 pendant l'écran de connexion déclenche la
  // redirection sur 401 générique de request() (lib/api.ts) et boucle.
  const settings = useSettingsQuery(authenticated)
  const appInfo = useAppInfoQuery(authenticated)

  // La langue enregistrée côté serveur l'emporte sur celle détectée dans ce
  // navigateur (localStorage / langue du navigateur) dès qu'elle est connue.
  const savedLanguage = appInfo.data?.language
  useEffect(() => {
    if (savedLanguage && savedLanguage !== language) setLanguage(savedLanguage)
  }, [savedLanguage, language, setLanguage])

  // Nouvelle version du conteneur installée pendant que l'onglet était ouvert :
  // proposition de recharger (le code chargé dans la page est l'ancien).
  const serverVersion = appInfo.data?.version
  const loadedVersion = useRef<string | null>(null)
  useEffect(() => {
    if (!serverVersion) return
    if (loadedVersion.current === null) {
      loadedVersion.current = serverVersion
    } else if (serverVersion !== loadedVersion.current) {
      toast(t("update.installed"), {
        id: "new-version",
        duration: Infinity,
        action: { label: t("update.reload"), onClick: () => window.location.reload() },
      })
    }
  }, [serverVersion, t])

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
        <p className="text-destructive">{t("common.apiUnreachable")}</p>
      </FullPageState>
    )
  }

  if (authStatus.data.setup_required) {
    return <SetupAdminPage />
  }

  if (!authenticated) {
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
        <p className="text-destructive">{t("common.apiUnreachable")}</p>
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
