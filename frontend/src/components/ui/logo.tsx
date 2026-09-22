import { cn } from "@/lib/utils"

// Image de développement (`ghcr.io/sec844/analysarr:dev`) : le point du logo
// passe au rouge. Version injectée au build (voir Dockerfile), donc connue
// avant même la connexion, contrairement à celle servie par l'API.
const IS_DEV_BUILD = import.meta.env.VITE_APP_VERSION === "dev"
const DOT_COLOR = IS_DEV_BUILD ? "#ef4444" : "#10b981"

// Marque Analysarr : trois arcs et un point, même géométrie que
// `public/favicon.svg` et `unraid/analysarr.png` — les trois doivent rester
// identiques. Les arcs prennent la couleur du texte (lisible en clair comme en
// sombre), le point garde le vert de marque dans les deux thèmes.
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" fill="none" aria-hidden className={cn("size-6 shrink-0", className)}>
      <circle
        cx="24"
        cy="24"
        r="15.5"
        stroke="currentColor"
        strokeWidth="5"
        strokeLinecap="round"
        strokeDasharray="21.46 11"
        transform="rotate(-96 24 24)"
      />
      <circle cx="24" cy="24" r="4.6" fill={DOT_COLOR} />
    </svg>
  )
}
