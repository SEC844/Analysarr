import { Eye, FileVideo, Loader2, Magnet, TriangleAlert } from "lucide-react"
import { Link } from "react-router-dom"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useDeleteIgnoreMutation, useIgnoresQuery } from "@/hooks/use-ignores"
import { useI18n } from "@/i18n"
import { formatDateTime } from "@/lib/format"
import type { IgnoreKind, IgnoreRuleRead } from "@/types/ignores"

const KIND_ICONS: Record<IgnoreKind, typeof Magnet> = { torrent: Magnet, file: FileVideo, status: TriangleAlert }

function IgnoredRow({ rule }: { rule: IgnoreRuleRead }) {
  const { t } = useI18n()
  const remove = useDeleteIgnoreMutation()
  const Icon = KIND_ICONS[rule.kind]
  const target = rule.status ? t(`status.${rule.status}`) : rule.label

  return (
    <li className="flex flex-wrap items-start gap-x-3 gap-y-2 py-3">
      <Icon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-sm font-medium break-all">{target}</p>
        <p className="text-muted-foreground text-xs">
          {t(`ignore.kinds.${rule.kind}`)} ·{" "}
          {rule.media_id !== null ? (
            <Link to={`/media/${rule.media_id}`} className="hover:underline">
              {rule.media_title}
            </Link>
          ) : (
            rule.media_title
          )}{" "}
          · {formatDateTime(rule.created_at)}
        </p>
        {rule.note && <p className="text-sm break-words">{rule.note}</p>}
        {!rule.present && <Badge variant="outline">{t("ignore.absent")}</Badge>}
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={remove.isPending}
        onClick={() =>
          remove.mutate(rule.id, {
            onSuccess: () => toast.success(t("ignore.undone")),
            onError: (err) => toast.error(err instanceof Error ? err.message : t("ignore.failed")),
          })
        }
      >
        {remove.isPending ? <Loader2 className="size-4 animate-spin" /> : <Eye className="size-4" />}
        {t("ignore.unignore")}
      </Button>
    </li>
  )
}

/** Réglages → Configuration → Éléments ignorés : tout ce qui a été ignoré, au
 * même endroit, pour le retrouver et revenir dessus. */
export function IgnoredSection() {
  const { t } = useI18n()
  const { data, isLoading } = useIgnoresQuery()

  if (isLoading) return <Skeleton className="h-64 w-full" />

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("ignore.title")}</CardTitle>
        <CardDescription>{t("ignore.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {!data || data.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("ignore.empty")}</p>
        ) : (
          <ul className="divide-border divide-y">
            {data.map((rule) => (
              <IgnoredRow key={rule.id} rule={rule} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
