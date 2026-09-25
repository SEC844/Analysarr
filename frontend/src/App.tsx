import { lazy, Suspense, useEffect, useRef } from "react"
import { Route, Routes } from "react-router-dom"
import { toast } from "sonner"

import { AppShell } from "@/components/layout/app-shell"
import { StarPrompt } from "@/components/star-prompt"
import { Skeleton } from "@/components/ui/skeleton"
import { useAppInfoQuery, usePreferences } from "@/hooks/use-app"
import { useAuthStatusQuery } from "@/hooks/use-auth"
import { useSettingsQuery } from "@/hooks/use-settings"
import { useI18n } from "@/i18n"
import { setDisplayTimeZone } from "@/lib/format"
import { LoginPage } from "@/pages/login-page"
import { MediaListPage } from "@/pages/media-list-page"
import { SetupAdminPage } from "@/pages/setup-admin-page"

// Pages chargées à la demande : la bibliothèque s'affiche sans attendre le code
// des réglages, de l'assistant et de la fiche (le bundle initial dépassait
// 800 Ko).
const MediaDetailPage = lazy(() => import("@/pages/media-detail-page").then((m) => ({ default: m.MediaDetailPage })))
const OnboardingWizard = lazy(() =>
  import("@/pages/onboarding-wizard").then((m) => ({ default: m.OnboardingWizard })),
)
const SettingsPage = lazy(() => import("@/pages/settings-page").then((m) => ({ default: m.SettingsPage })))

function PageSkeleton() {
  return (
    <div className="space-y-4 p-4" aria-busy="true">
      <Skeleton className="h-8 w-1/3" />
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-64 w-full" />
    </div>
  )
}

function FullPageState({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-svh items-center justify-center px-4">{children}</div>
}

function App() {
  // Fuseau d'affichage : posé avant tout rendu, les formats de dates servent
  // aussi hors composants React (voir lib/format.ts).
  const preferences = usePreferences()
  setDisplayTimeZone(preferences.timezone)
  const { t, language, setLanguage, setMediaServer, setRequestManager } = useI18n()
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

  // Nom du serveur multimédia (Emby ou Jellyfin) injecté dans tous les textes.
  const mediaServer = settings.data?.media_server
  useEffect(() => {
    if (mediaServer) setMediaServer(mediaServer)
  }, [mediaServer, setMediaServer])

  // Nom du gestionnaire de demandes (Seer ou Ombi), variable `{requests}`.
  const requestManager = settings.data?.seer.kind
  useEffect(() => {
    if (requestManager) setRequestManager(requestManager)
  }, [requestManager, setRequestManager])

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
    return (
      <Suspense fallback={<PageSkeleton />}>
        <OnboardingWizard existing={settings.data} />
      </Suspense>
    )
  }

  return (
    <AppShell>
      <Suspense fallback={<PageSkeleton />}>
        <Routes>
          <Route path="/" element={<MediaListPage />} />
          <Route path="/media/:id" element={<MediaDetailPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </Suspense>
      {/* Invitation à mettre une étoile : jamais à l'arrivée, jamais deux fois
          (voir components/star-prompt.tsx). */}
      <StarPrompt />
    </AppShell>
  )
}

export default App
