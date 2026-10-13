import { Link } from "react-router-dom"
import { Loader2, ShieldCheck } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  useDismissSeedPromptMutation,
  useSaveSeedProtectionMutation,
  useSeedProtectionQuery,
} from "@/hooks/use-seed-protection"
import { useI18n } from "@/i18n"

const SEED_SETTINGS = "/settings?section=seed-protection"

/** Proposé une seule fois aux installations existantes : la protection du
 * seed changerait ce que font leurs nettoyages et leurs automatisations, elle
 * n'est donc jamais activée sans leur accord. Accueil (lien vers les réglages)
 * et section des réglages. */
export function SeedProtectionBanner({ withSettingsLink = true }: { withSettingsLink?: boolean }) {
  const { t } = useI18n()
  const { data } = useSeedProtectionQuery()
  const saveMutation = useSaveSeedProtectionMutation()
  const dismissMutation = useDismissSeedPromptMutation()

  if (!data?.prompt) return null

  const failed = (err: unknown) => toast.error(err instanceof Error ? err.message : t("common.saveFailed"))
  const busy = saveMutation.isPending || dismissMutation.isPending

  return (
    <div
      className="flex flex-wrap items-start gap-3 rounded-lg border border-sky-500/30 bg-sky-500/10 p-3 text-sm text-sky-800 dark:text-sky-200"
      role="status"
    >
      <ShieldCheck className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1 space-y-1">
        <p className="font-medium">{t("seed.prompt.title")}</p>
        <p className="text-sky-800/90 dark:text-sky-200/90">
          {t("seed.prompt.body", { days: data.private_min_days })}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          disabled={busy}
          onClick={() =>
            saveMutation.mutate(
              {
                enabled: true,
                private_min_days: data.private_min_days,
                public_enabled: data.public_enabled,
                public_min_days: data.public_min_days,
                tracker_rules: data.tracker_rules,
              },
              { onSuccess: () => toast.success(t("seed.prompt.enabled")), onError: failed },
            )
          }
        >
          {saveMutation.isPending && <Loader2 className="size-4 animate-spin" />}
          {t("seed.prompt.enable")}
        </Button>
        {withSettingsLink && (
          <Button type="button" variant="outline" size="sm" render={<Link to={SEED_SETTINGS} />}>
            {t("seed.prompt.configure")}
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => dismissMutation.mutate(undefined, { onError: failed })}
        >
          {t("seed.prompt.dismiss")}
        </Button>
      </div>
    </div>
  )
}
