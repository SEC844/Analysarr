import { AlertTriangle, CheckCircle2, Copy, Hourglass, Link2, PackageX, Radio, Tv2, Unlink, Wifi } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"
import { INFO_STATUSES, type MediaStatus } from "@/types/media"

const STATUS_CONFIG: Record<MediaStatus, { icon: typeof Copy; className: string }> = {
  doublon: {
    icon: Copy,
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  orphelin_qbit: {
    icon: AlertTriangle,
    className: "bg-destructive/10 text-destructive",
  },
  non_hardlink: {
    icon: Link2,
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  // Couverture tracker : information, jamais une alerte. Un média sain porte
  // donc deux badges : « Sain » + « Cross-seed » ou « Tracker unique ».
  cross_seed: {
    icon: Radio,
    className: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  tracker_unique: {
    icon: Radio,
    className: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
  },
  manquant_emby: {
    icon: Tv2,
    className: "bg-destructive/10 text-destructive",
  },
  manquant_qbit: {
    icon: Wifi,
    className: "bg-destructive/10 text-destructive",
  },
  // Présent dans la bibliothèque mais suivi par personne : à traiter (ajout
  // dans Radarr/Sonarr), pas un contenu perdu.
  manquant_arr: {
    icon: Unlink,
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  // Le fichier est bien là, c'est son rangement qui a échoué : ambre
  // (à traiter) plutôt que rouge (contenu perdu).
  import_rate: {
    icon: PackageX,
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  // Information, pas problème de bibliothèque : le contenu est en route.
  telechargement_bloque: {
    icon: Hourglass,
    className: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
  },
}

export function StatusBadge({ status }: { status: MediaStatus }) {
  const { t } = useI18n()
  const config = STATUS_CONFIG[status]
  const Icon = config.icon
  return (
    <Badge variant="outline" className={cn("border-transparent", config.className)}>
      <Icon className="size-3" />
      {t(`status.${status}`)}
    </Badge>
  )
}

export function StatusBadgeList({ statuses }: { statuses: MediaStatus[] }) {
  const { t } = useI18n()
  // Sain = aucune alerte. La couverture tracker s'affiche à côté, comme un
  // complément d'information (même découpage que le backend).
  const alerts = statuses.filter((status) => !INFO_STATUSES.includes(status))
  const info = statuses.filter((status) => INFO_STATUSES.includes(status))

  return (
    <div className="flex flex-wrap gap-1">
      {alerts.length === 0 && (
        <Badge variant="outline" className="border-transparent bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
          <CheckCircle2 className="size-3" />
          {t("status.sain")}
        </Badge>
      )}
      {[...alerts, ...info].map((status) => (
        <StatusBadge key={status} status={status} />
      ))}
    </div>
  )
}
