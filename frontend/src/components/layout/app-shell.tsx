import type { ReactNode } from "react"
import { LogOut } from "lucide-react"
import { Link, NavLink } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { Logo } from "@/components/ui/logo"
import { PulseDot } from "@/components/ui/pulse-dot"
import { useAppInfoQuery } from "@/hooks/use-app"
import { useLogoutMutation } from "@/hooks/use-auth"
import { useServicesStatusQuery } from "@/hooks/use-services"
import { useI18n } from "@/i18n"
import { formatVersion } from "@/lib/format"
import { summarizeServices } from "@/lib/services"
import { cn } from "@/lib/utils"

const APPLICATION_SETTINGS = "/settings?section=application"

export function AppShell({ children }: { children: ReactNode }) {
  const { t } = useI18n()
  const logoutMutation = useLogoutMutation()
  const { data: appInfo } = useAppInfoQuery()
  const updateAvailable = appInfo?.update?.update_available ?? false

  const { data: services } = useServicesStatusQuery()
  const { firstDownSection } = summarizeServices(services)
  const downCount = services?.services.filter((s) => !s.ok).length ?? 0

  // Service injoignable : le lien mène directement à sa section (prioritaire).
  // Mise à jour disponible : le lien mène à l'onglet Application.
  const settingsTarget = firstDownSection
    ? `/settings?section=${firstDownSection}`
    : updateAvailable
      ? APPLICATION_SETTINGS
      : "/settings"
  const navLinks = [
    { to: "/", label: t("nav.home"), serviceDown: false, update: false },
    { to: settingsTarget, label: t("nav.settings"), serviceDown: downCount > 0, update: updateAvailable },
  ]

  return (
    <div className="bg-background min-h-svh">
      <header className="border-border border-b">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between px-4 py-3 sm:px-6">
          <div className="flex items-baseline gap-2">
            <Link to="/" className="flex items-center gap-2 text-lg font-semibold tracking-tight hover:opacity-80">
              <Logo className="size-6 self-center" />
              Analysarr
            </Link>
            {appInfo && (
              <Link to={APPLICATION_SETTINGS} className="text-muted-foreground hover:text-foreground text-xs">
                {formatVersion(appInfo.version)}
              </Link>
            )}
          </div>
          <div className="flex items-center gap-2">
            <nav className="flex gap-1">
              {navLinks.map((link) => (
                <NavLink
                  key={link.label}
                  to={link.to}
                  end={link.to === "/"}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                      isActive
                        ? "bg-secondary text-secondary-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )
                  }
                >
                  {link.label}
                  {link.serviceDown && (
                    <PulseDot tone="danger" label={t("servicesStatus.someDown", { count: downCount })} />
                  )}
                  {link.update && <PulseDot label={t("nav.updateAvailable")} />}
                </NavLink>
              ))}
            </nav>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => logoutMutation.mutate()}
              disabled={logoutMutation.isPending}
              title={t("nav.logout")}
            >
              <LogOut className="size-4" />
            </Button>
          </div>
        </div>
      </header>
      <main>{children}</main>
    </div>
  )
}
