import { Send } from "lucide-react"

import { cn } from "@/lib/utils"
import type { ChannelKind } from "@/types/notifications"

// Logos officiels repris de simple-icons (CC0-1.0), copiés ici plutôt
// qu'installés : trois tracés valent mieux qu'une dépendance de 16 Mo. Gotify
// n'y figure pas — son canal garde une icône générique plutôt qu'un logo
// inventé.
const BRANDS: Partial<Record<ChannelKind, { color: string; path: string }>> = {
  discord: {
    color: "#5865F2",
    path: "M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z",
  },
  ntfy: {
    color: "#317F6F",
    path: "M12.597 13.693v2.156h6.205v-2.156ZM5.183 6.549v2.363l3.591 1.901.023.01-.023.009-3.591 1.901v2.35l.386-.211 5.456-2.969V9.729ZM3.659 2.037C1.915 2.037.42 3.41.42 5.154v.002L.438 18.73 0 21.963l5.956-1.583h14.806c1.744 0 3.238-1.374 3.238-3.118V5.154c0-1.744-1.493-3.116-3.237-3.117h-.001zm0 2.2h17.104c.613.001 1.037.447 1.037.917v12.108c0 .47-.424.916-1.038.916H5.633l-3.026.915.031-.179-.017-13.76c0-.47.424-.917 1.038-.917z",
  },
}

const GOTIFY_COLOR = "#0ea5e9"

export function channelColor(kind: ChannelKind): string {
  return BRANDS[kind]?.color ?? GOTIFY_COLOR
}

/** Logo de la marque, aux couleurs du service. */
export function ChannelLogo({ kind, className }: { kind: ChannelKind; className?: string }) {
  const brand = BRANDS[kind]
  if (!brand) return <Send className={cn("size-5", className)} style={{ color: GOTIFY_COLOR }} aria-hidden />
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden
      className={cn("size-5", className)}
      style={{ color: brand.color }}
    >
      <path d={brand.path} />
    </svg>
  )
}

/** Logo dans une pastille teintée, pour les cartes et la grille. */
export function ChannelLogoTile({ kind, className }: { kind: ChannelKind; className?: string }) {
  return (
    <span
      className={cn("flex size-10 shrink-0 items-center justify-center rounded-lg", className)}
      style={{ backgroundColor: `${channelColor(kind)}1a` }}
    >
      <ChannelLogo kind={kind} />
    </span>
  )
}
