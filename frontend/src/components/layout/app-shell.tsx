import type { ReactNode } from "react"
import { LogOut } from "lucide-react"
import { Link, NavLink } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { PulseDot } from "@/components/ui/pulse-dot"
import { useAppInfoQuery } from "@/hooks/use-app"
import { useLogoutMutation } from "@/hooks/use-auth"
import { useI18n } from "@/i18n"
import { formatVersion } from "@/lib/format"
import { cn } from "@/lib/utils"

const APPLICATION_SETTINGS = "/settings?section=application"

export function AppShell({ children }: { children: ReactNode }) {
  const { t } = useI18n()
  const logoutMutation = useLogoutMutation()
  const { data: appInfo } = useAppInfoQuery()
  const updateAvailable = appInfo?.update?.update_available ?? false

  const navLinks = [
    { to: "/", label: t("nav.home"), badge: false },
    // Mise à jour disponible : le lien mène directement à l'onglet Application.
    { to: updateAvailable ? APPLICATION_SETTINGS : "/settings", label: t("nav.settings"), badge: updateAvailable },
  ]

  return (
    <div className="bg-background min-h-svh">
      <header className="border-border border-b">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between px-4 py-3 sm:px-6">
          <div className="flex items-baseline gap-2">
            <Link to="/" className="text-lg font-semibold tracking-tight hover:opacity-80">
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
                  {link.badge && <PulseDot label={t("nav.updateAvailable")} />}
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
