import { AlertTriangle, CheckCircle2, Copy, Radio, Tv2, Wifi } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import type { MediaStatus } from "@/types/media"

const STATUS_CONFIG: Record<MediaStatus, { label: string; icon: typeof Copy; className: string }> = {
  doublon: {
    label: "Doublon",
    icon: Copy,
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  orphelin_qbit: {
    label: "Orphelin qBit",
    icon: AlertTriangle,
    className: "bg-destructive/10 text-destructive",
  },
  tracker_unique: {
    label: "Tracker unique",
    icon: Radio,
    className: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
  },
  manquant_emby: {
    label: "Absent d'Emby",
    icon: Tv2,
    className: "bg-destructive/10 text-destructive",
  },
  manquant_qbit: {
    label: "Non seedé",
    icon: Wifi,
    className: "bg-destructive/10 text-destructive",
  },
}

export function StatusBadge({ status }: { status: MediaStatus }) {
  const config = STATUS_CONFIG[status]
  const Icon = config.icon
  return (
    <Badge variant="outline" className={cn("border-transparent", config.className)}>
      <Icon className="size-3" />
      {config.label}
    </Badge>
  )
}

export function StatusBadgeList({ statuses }: { statuses: MediaStatus[] }) {
  if (statuses.length === 0) {
    return (
      <Badge variant="outline" className="border-transparent bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="size-3" />
        Sain
      </Badge>
    )
  }
  return (
    <div className="flex flex-wrap gap-1">
      {statuses.map((s) => (
        <StatusBadge key={s} status={s} />
      ))}
    </div>
  )
}
