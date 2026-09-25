import { Badge } from "@/components/ui/badge"
import { UserAvatar } from "@/components/media/watch-stats"
import { usePreferences } from "@/hooks/use-app"
import { useI18n } from "@/i18n"
import { formatDate, formatDateTime, formatRelativeTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { MediaRequestRead } from "@/types/media"

function RequestLine({ request }: { request: MediaRequestRead }) {
  const { t } = useI18n()
  const { absolute_dates } = usePreferences()

  const who = request.requested_by
    ? t("seer.requestedBy", { name: request.requested_by.name })
    : t("seer.requestedUnknown")
  const when = request.requested_at
    ? absolute_dates
      ? t("seer.on", { date: formatDate(request.requested_at) ?? "" })
      : formatRelativeTime(request.requested_at)
    : null

  const approver = request.modified_by?.name
  const outcome =
    request.status === "pending"
      ? t("seer.pending")
      : request.status === "declined"
        ? approver
          ? t("seer.declinedBy", { name: approver })
          : t("seer.declined")
        : request.status === "failed"
          ? t("seer.failed")
          : request.auto_approved
            ? t("seer.autoApproved")
            : approver
              ? t("seer.approvedBy", { name: approver })
              : // Ombi ne dit pas qui a approuvé, ni si c'était automatique.
                t("seer.approved")

  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      {request.requested_by && (
        <UserAvatar
          user={{
            id: request.requested_by.emby_user_id ?? "",
            name: request.requested_by.name,
            // Avatar seulement si le compte Emby correspondant a été retrouvé.
            image_tag: request.requested_by.emby_user_id ? request.requested_by.image_tag : null,
          }}
          className="size-5 text-[10px]"
        />
      )}
      <span className="text-muted-foreground" title={formatDateTime(request.requested_at) ?? undefined}>
        {[who, when].filter(Boolean).join(" ")} ·{" "}
        <span className={cn(request.status === "declined" || request.status === "failed" ? "text-destructive" : undefined)}>
          {outcome}
        </span>
        {request.seasons.length > 0 &&
          ` · ${t("seer.seasons", { count: request.seasons.length, list: request.seasons.join(", ") })}`}
      </span>
      {request.is_4k && <Badge variant="outline">4K</Badge>}
    </li>
  )
}

// Demandes du média (gestionnaire de demandes activé uniquement : liste vide sinon).
export function MediaRequests({ requests }: { requests: MediaRequestRead[] }) {
  if (requests.length === 0) return null
  return (
    <ul className="space-y-1">
      {requests.map((request, i) => (
        <RequestLine key={i} request={request} />
      ))}
    </ul>
  )
}
