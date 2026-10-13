import type { ReactNode } from "react"
import type { LucideIcon } from "lucide-react"

import { cn } from "@/lib/utils"

export interface SideNavItem<T extends string> {
  id: T
  label: string
  icon?: LucideIcon
  /** Pastille d'état affichée en bout de ligne (service en défaut, mise à jour...). */
  adornment?: ReactNode
}

export interface SideNavGroup<T extends string> {
  /** Titre du groupe ; absent pour une navigation d'un seul groupe. */
  label?: string
  items: SideNavItem<T>[]
}

// Largeur d'écran à partir de laquelle la navigation passe à gauche. Le
// nettoyage attend plus longtemps : sa grille a besoin de la place.
const LAYOUTS = {
  sm: {
    row: "flex flex-col gap-8 sm:flex-row",
    nav: "sm:w-48 sm:flex-col sm:gap-6 sm:overflow-visible",
    items: "sm:flex-col",
  },
  lg: {
    row: "flex flex-col gap-6 lg:flex-row lg:gap-8",
    nav: "lg:w-48 lg:flex-col lg:gap-6 lg:overflow-visible",
    items: "lg:flex-col",
  },
} as const

/** Page à sections (Réglages, Nettoyage) : la navigation en colonne à gauche
 * sur un écran large, en rangée défilante au-dessus du contenu sinon. */
export function SideNavLayout<T extends string>({
  groups,
  value,
  onChange,
  label,
  breakpoint = "sm",
  className,
  children,
}: {
  groups: SideNavGroup<T>[]
  value: T
  onChange: (id: T) => void
  label: string
  breakpoint?: keyof typeof LAYOUTS
  /** Classes du contenu (espacement entre ses blocs). */
  className?: string
  children: ReactNode
}) {
  const layout = LAYOUTS[breakpoint]
  return (
    <div className={layout.row}>
      <nav aria-label={label} className={cn("flex shrink-0 flex-row gap-4 overflow-x-auto", layout.nav)}>
        {groups.map((group, index) => (
          // Groupe titré : une colonne sous son titre. Groupe unique sans
          // titre : ses onglets côte à côte tant que la navigation est en haut.
          <div
            key={group.label ?? index}
            className={cn("flex gap-0.5", group.label ? "flex-col" : cn("flex-row", layout.items))}
          >
            {group.label && (
              <p className="text-muted-foreground px-2 pb-1 text-xs font-medium tracking-wide uppercase">
                {group.label}
              </p>
            )}
            {group.items.map((item) => {
              const Icon = item.icon
              const active = item.id === value
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-current={active ? "page" : undefined}
                  onClick={() => onChange(item.id)}
                  className={cn(
                    "flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm whitespace-nowrap transition-colors",
                    active
                      ? "bg-secondary text-secondary-foreground"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted",
                  )}
                >
                  {Icon && <Icon className="size-4 shrink-0" />}
                  <span className="flex-1">{item.label}</span>
                  {item.adornment}
                </button>
              )
            })}
          </div>
        ))}
      </nav>
      <div className={cn("min-w-0 flex-1", className)}>{children}</div>
    </div>
  )
}
