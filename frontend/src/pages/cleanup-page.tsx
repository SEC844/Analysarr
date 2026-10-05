import { useState } from "react"
import { Link } from "react-router-dom"
import { Clapperboard, Loader2, Play, ShieldCheck, Trash2, Tv } from "lucide-react"

import { CleanupDeleteDialog } from "@/components/cleanup/cleanup-delete-dialog"
import { CleanupGoalCard } from "@/components/cleanup/cleanup-goal-card"
import { CleanupSettingsPanel } from "@/components/cleanup/cleanup-settings-panel"
import { ScoreBreakdown } from "@/components/cleanup/score-breakdown"
import { ForecastSection } from "@/components/forecast/forecast-section"
import { CleanupOptions } from "@/components/media/ignore-actions"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { useCleanupCandidatesQuery } from "@/hooks/use-cleanup"
import { useRecentlyUpdated } from "@/hooks/use-recently-updated"
import { useI18n, type MessageKey } from "@/i18n"
import { posterUrl } from "@/lib/api"
import { mainReasonText, protectionText, selectionSize } from "@/lib/cleanup"
import { formatBytes } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { CleanupCandidate, CleanupQuery, CleanupSort } from "@/types/cleanup"

const PAGE_SIZE = 50
const SORTS: CleanupSort[] = ["rank", "score", "space", "title"]
const TYPES = ["all", "movie", "series"] as const

function Poster({ candidate }: { candidate: CleanupCandidate }) {
  const Icon = candidate.media_type === "series" ? Tv : Clapperboard
  return candidate.has_poster ? (
    <img
      src={posterUrl(candidate.media_id, candidate.poster_image_tag)}
      alt=""
      loading="lazy"
      className="h-16 w-11 shrink-0 rounded object-cover"
    />
  ) : (
    <span className="bg-muted flex h-16 w-11 shrink-0 items-center justify-center rounded">
      <Icon className="text-muted-foreground size-5" />
    </span>
  )
}

function CandidateRow({
  candidate,
  selected,
  onToggle,
}: {
  candidate: CleanupCandidate
  selected: boolean
  onToggle: (candidate: CleanupCandidate) => void
}) {
  const { t } = useI18n()
  const isProtected = candidate.protections.length > 0
  const recentlyUpdated = useRecentlyUpdated(candidate.media_id)
  const reason = mainReasonText(t, candidate)

  return (
    <li className={cn("flex items-center gap-3 rounded-md py-3", isProtected && "opacity-70", recentlyUpdated && "live-updated")}>
      {isProtected ? (
        <span className="size-4 shrink-0" />
      ) : (
        <Checkbox
          checked={selected}
          onCheckedChange={() => onToggle(candidate)}
          aria-label={t("cleanup.select", { title: candidate.title })}
        />
      )}
      <Poster candidate={candidate} />
      <div className="min-w-0 flex-1 space-y-0.5">
        <Link to={`/media/${candidate.media_id}`} className="block truncate font-medium hover:underline">
          {candidate.title}
          {candidate.year ? <span className="text-muted-foreground font-normal"> ({candidate.year})</span> : null}
        </Link>
        {isProtected ? (
          <ul className="space-y-0.5 text-xs text-sky-700 dark:text-sky-300">
            {candidate.protections.map((protection) => (
              <li key={protection.kind} className="flex items-start gap-1">
                <ShieldCheck className="mt-0.5 size-3 shrink-0" />
                {protectionText(t, protection)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground truncate text-xs">
            {reason}
            {candidate.arr_instance_name ? ` · ${candidate.arr_instance_name}` : ""}
          </p>
        )}
        {candidate.malus_in_progress && (
          <p className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-300">
            <Play className="size-3" /> {t("cleanup.inProgressMalus")}
          </p>
        )}
      </div>
      <span className="text-muted-foreground hidden shrink-0 text-sm tabular-nums sm:block">
        {formatBytes(candidate.reclaimable_bytes)}
      </span>
      {isProtected ? (
        <span className="text-muted-foreground shrink-0 text-xs">{t("cleanup.protectedBadge")}</span>
      ) : (
        <ScoreBreakdown candidate={candidate} />
      )}
      {!isProtected && <CleanupOptions mediaId={candidate.media_id} />}
    </li>
  )
}

export function CleanupPage() {
  const { t } = useI18n()
  const [query, setQuery] = useState<CleanupQuery>({
    page: 1,
    page_size: PAGE_SIZE,
    sort: "rank",
    include_protected: false,
  })
  // Sélection conservée d'une page à l'autre.
  const [selected, setSelected] = useState<Map<number, CleanupCandidate>>(new Map())
  const [deleting, setDeleting] = useState(false)
  const { data, isLoading, isError, isFetching } = useCleanupCandidatesQuery(query)

  const update = (changes: Partial<CleanupQuery>) => setQuery((current) => ({ ...current, ...changes, page: 1 }))
  const toggle = (candidate: CleanupCandidate) =>
    setSelected((current) => {
      const next = new Map(current)
      if (next.has(candidate.media_id)) next.delete(candidate.media_id)
      else next.set(candidate.media_id, candidate)
      return next
    })
  const pages = data ? Math.max(1, Math.ceil(data.total / query.page_size)) : 1
  const typeLabel = (value: string) => t(`cleanup.type.${value}` as MessageKey)

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 pb-28 sm:px-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t("cleanup.title")}</h1>
        <p className="text-muted-foreground text-sm">{t("cleanup.description")}</p>
      </div>

      <ForecastSection />

      <CleanupGoalCard onSelect={(items) => setSelected(new Map(items.map((item) => [item.media_id, item])))} />

      <CleanupSettingsPanel />

      <div className="flex flex-wrap items-center gap-3">
        <Input
          type="search"
          aria-label={t("cleanup.search")}
          placeholder={t("cleanup.search")}
          className="w-56"
          maxLength={100}
          value={query.search ?? ""}
          onChange={(e) => update({ search: e.target.value })}
        />
        <Select
          value={query.media_type ?? "all"}
          onValueChange={(value) =>
            update({ media_type: value === "movie" || value === "series" ? value : undefined })
          }
        >
          <SelectTrigger className="w-44" aria-label={typeLabel("all")}>
            <SelectValue>{typeLabel}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {TYPES.map((value) => (
              <SelectItem key={value} value={value}>
                {typeLabel(value)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={query.sort} onValueChange={(value) => update({ sort: value as CleanupSort })}>
          <SelectTrigger className="w-44" aria-label={t("cleanup.sort.rank")}>
            <SelectValue>{(value: string) => t(`cleanup.sort.${value}` as MessageKey)}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {SORTS.map((value) => (
              <SelectItem key={value} value={value}>
                {t(`cleanup.sort.${value}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="flex items-center gap-2">
          <Switch
            id="cleanup-show-protected"
            checked={query.include_protected}
            onCheckedChange={(checked) => update({ include_protected: checked })}
          />
          <Label htmlFor="cleanup-show-protected" className="font-normal">
            {t("cleanup.showProtected")}
          </Label>
        </div>
        {isFetching && !isLoading && <Loader2 className="text-muted-foreground size-4 animate-spin" />}
      </div>

      {data && (
        <p className="text-sm" role="status">
          <span className="font-medium">
            {t("cleanup.summary", { count: data.candidate_count, size: formatBytes(data.total_reclaimable_bytes) })}
          </span>
          {data.protected_count > 0 && (
            <span className="text-muted-foreground"> · {t("cleanup.protectedCount", { count: data.protected_count })}</span>
          )}
        </p>
      )}

      {isLoading && <Skeleton className="h-64 w-full" />}
      {isError && <p className="text-destructive text-sm">{t("cleanup.loadFailed")}</p>}
      {data && data.items.length === 0 && (
        <div className="text-muted-foreground space-y-1 py-8 text-center text-sm">
          <p>{t("cleanup.empty")}</p>
          <p>{t("cleanup.emptyHint")}</p>
        </div>
      )}
      {data && data.items.length > 0 && (
        <ul className="divide-border divide-y">
          {data.items.map((candidate) => (
            <CandidateRow
              key={candidate.media_id}
              candidate={candidate}
              selected={selected.has(candidate.media_id)}
              onToggle={toggle}
            />
          ))}
        </ul>
      )}

      {data && pages > 1 && (
        <div className="flex items-center justify-center gap-3 text-sm">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={query.page <= 1}
            onClick={() => setQuery((current) => ({ ...current, page: current.page - 1 }))}
          >
            {t("cleanup.pagination.previous")}
          </Button>
          <span className="text-muted-foreground">{t("cleanup.pagination.page", { page: query.page, pages })}</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={query.page >= pages}
            onClick={() => setQuery((current) => ({ ...current, page: current.page + 1 }))}
          >
            {t("cleanup.pagination.next")}
          </Button>
        </div>
      )}

      {selected.size > 0 && (
        <div className="bg-background/95 border-border fixed inset-x-0 bottom-0 z-40 border-t backdrop-blur">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium" role="status">
                {t("cleanup.selection", { count: selected.size, size: selectionSize(selected.values()) })}
              </p>
              <p className="text-muted-foreground text-xs">{t("cleanup.selectionHint")}</p>
            </div>
            <Button type="button" variant="ghost" size="sm" onClick={() => setSelected(new Map())}>
              {t("cleanup.clearSelection")}
            </Button>
            <Button type="button" variant="destructive" size="sm" onClick={() => setDeleting(true)}>
              <Trash2 className="size-4" />
              {t("cleanup.deleteSelection")}
            </Button>
          </div>
        </div>
      )}

      <CleanupDeleteDialog
        candidates={[...selected.values()]}
        open={deleting}
        onClose={() => setDeleting(false)}
        onDeleted={(ids) =>
          setSelected((current) => {
            const next = new Map(current)
            for (const id of ids) next.delete(id)
            return next
          })
        }
      />
    </div>
  )
}
