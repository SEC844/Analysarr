import { cn } from "@/lib/utils"

// Pastille clignotante signalant une nouveauté (mise à jour disponible).
// L'animation est coupée pour les utilisateurs qui réduisent les animations.
export function PulseDot({ label, className }: { label: string; className?: string }) {
  return (
    <span className={cn("relative flex size-2 shrink-0", className)} role="status" aria-label={label} title={label}>
      <span className="absolute inline-flex size-full animate-ping rounded-full bg-sky-500 opacity-75 motion-reduce:animate-none" />
      <span className="relative inline-flex size-2 rounded-full bg-sky-500" />
    </span>
  )
}
