import { cn } from "@/lib/utils"

const TONES = {
  // Nouveauté (mise à jour disponible).
  info: "bg-sky-500",
  // Attention (automatisations en pause).
  warning: "bg-amber-500",
  // Problème (service injoignable).
  danger: "bg-red-500",
} as const

// Pastille clignotante signalant une nouveauté ou un problème.
// L'animation est coupée pour les utilisateurs qui réduisent les animations.
export function PulseDot({
  label,
  tone = "info",
  className,
}: {
  label: string
  tone?: keyof typeof TONES
  className?: string
}) {
  return (
    <span className={cn("relative flex size-2 shrink-0", className)} role="status" aria-label={label} title={label}>
      <span
        className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-75 motion-reduce:animate-none", TONES[tone])}
      />
      <span className={cn("relative inline-flex size-2 rounded-full", TONES[tone])} />
    </span>
  )
}
