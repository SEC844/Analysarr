import type { ReactNode } from "react"
import { LogOut } from "lucide-react"
import { Link, NavLink } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { useLogoutMutation } from "@/hooks/use-auth"
import { cn } from "@/lib/utils"

const NAV_LINKS = [
  { to: "/", label: "Accueil" },
  { to: "/settings", label: "Réglages" },
]

export function AppShell({ children }: { children: ReactNode }) {
  const logoutMutation = useLogoutMutation()

  return (
    <div className="bg-background min-h-svh">
      <header className="border-border border-b">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between px-4 py-3 sm:px-6">
          <Link to="/" className="text-lg font-semibold tracking-tight hover:opacity-80">
            Analysarr
          </Link>
          <div className="flex items-center gap-2">
            <nav className="flex gap-1">
              {NAV_LINKS.map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  end={link.to === "/"}
                  className={({ isActive }) =>
                    cn(
                      "rounded-md px-3 py-1.5 text-sm transition-colors",
                      isActive
                        ? "bg-secondary text-secondary-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )
                  }
                >
                  {link.label}
                </NavLink>
              ))}
            </nav>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => logoutMutation.mutate()}
              disabled={logoutMutation.isPending}
              title="Se déconnecter"
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
