import { useCallback, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { Loader2, Search, SlidersHorizontal, Trash2, TriangleAlert, X } from "lucide-react"

import { CandidateCard } from "@/components/cleanup/candidate-card"
import { CleanupDeleteDialog } from "@/components/cleanup/cleanup-delete-dialog"
import { CleanupSettingsPanel } from "@/components/cleanup/cleanup-settings-panel"
import { GridSizeToggle } from "@/components/media/grid-size-toggle"
import { MediaGrid } from "@/components/media/media-grid"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { useCleanupCandidatesQuery } from "@/hooks/use-cleanup"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { useGridSize } from "@/hooks/use-grid-size"
import { useScrollRestoration } from "@/hooks/use-scroll-restoration"
import { useSessionState } from "@/hooks/use-session-state"
import { useI18n, type MessageKey } from "@/i18n"
import { selectionSize } from "@/lib/cleanup"
import { formatBytes } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { CleanupCandidate, CleanupQuery, CleanupSort } from "@/types/cleanup"

// Multiple de 2, 3, 4, 5, 6 et 10 colonnes : la dernière rangée d'une page
// est le plus souvent pleine. Le serveur accepte jusqu'à 100.
const PAGE_SIZE = 60
const SEARCH_DELAY_MS = 250
const DEFAULT_SORT: CleanupSort = "rank"
const SORTS: CleanupSort[] = ["rank", "score", "space", "title"]
const TYPES = ["all", "movie", "series"] as const
const SETTINGS_PANEL_ID = "cleanup-ranking-settings"

type Selection = Map<number, CleanupCandidate>

/** Filtres de l'assistant, dans l'URL comme ceux de la bibliothèque : ils
 * survivent à la visite d'une fiche, au lien de l'en-tête et au rechargement.
 * Les valeurs par défaut n'y figurent pas. */
interface Filters {
  search: string
  type: "movie" | "series" | undefined
  sort: CleanupSort
  protected: boolean
  page: number
}

function readFilters(params: URLSearchParams): Filters {
  const type = params.get("type")
  const sort = params.get("sort")
  const page = Number(params.get("page"))
  return {
    search: params.get("q") ?? "",
    type: type === "movie" || type === "series" ? type : undefined,
    sort: SORTS.find((value) => value === sort) ?? DEFAULT_SORT,
    protected: params.get("protected") === "1",
    page: Number.isInteger(page) && page > 1 ? page : 1,
  }
}

function writeFilters(params: URLSearchParams, filters: Filters): URLSearchParams {
  const next = new URLSearchParams(params)
  const set = (key: string, value: string | undefined) => (value ? next.set(key, value) : next.delete(key))
  set("q", filters.search || undefined)
  set("type", filters.type)
  set("sort", filters.sort === DEFAULT_SORT ? undefined : filters.sort)
  set("protected", filters.protected ? "1" : undefined)
  set("page", filters.page > 1 ? String(filters.page) : undefined)
  return next
}

function Pagination({ page, pages, onPage }: { page: number; pages: number; onPage: (page: number) => void }) {
  const { t } = useI18n()
  return (
    <div className="flex items-center justify-center gap-3 text-sm">
      <Button type="button" variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        {t("cleanup.pagination.previous")}
      </Button>
      <span className="text-muted-foreground tabular-nums">{t("cleanup.pagination.page", { page, pages })}</span>
      <Button type="button" variant="outline" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        {t("cleanup.pagination.next")}
      </Button>
    </div>
  )
}

/** Assistant de suppression : les candidats classés, en grille comme la
 * bibliothèque, sélection conservée d'une page et d'une visite à l'autre. */
export function CleanupAssistant() {
  const { t } = useI18n()
  const [params, setParams] = useSearchParams()
  const filters = readFilters(params)
  const search = useDebouncedValue(filters.search, SEARCH_DELAY_MS)
  const query: CleanupQuery = {
    page: filters.page,
    page_size: PAGE_SIZE,
    sort: filters.sort,
    media_type: filters.type,
    search: search || undefined,
    include_protected: filters.protected,
  }
  const { data, isLoading, isError, isFetching } = useCleanupCandidatesQuery(query)
  const [gridSize, setGridSize] = useGridSize()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [selected, setSelected] = useSessionState<Selection>("cleanup-selection", new Map())

  useScrollRestoration(!isLoading)

  // Tout changement de filtre ramène à la première page.
  const update = (changes: Partial<Filters>) =>
    setParams((current) => writeFilters(current, { ...readFilters(current), page: 1, ...changes }), { replace: true })
  const toggle = useCallback(
    (candidate: CleanupCandidate) =>
      setSelected((current) => {
        const next = new Map(current)
        if (next.has(candidate.media_id)) next.delete(candidate.media_id)
        else next.set(candidate.media_id, candidate)
        return next
      }),
    [setSelected],
  )

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1
  const selectable = data?.items.filter((candidate) => candidate.protections.length === 0) ?? []
  const pageSelected = selectable.length > 0 && selectable.every((candidate) => selected.has(candidate.media_id))
  const togglePage = () =>
    setSelected((current) => {
      const next = new Map(current)
      for (const candidate of selectable) {
        if (pageSelected) next.delete(candidate.media_id)
        else next.set(candidate.media_id, candidate)
      }
      return next
    })
  const typeLabel = (value: string) => t(`cleanup.type.${value}` as MessageKey)

  return (
    <section aria-labelledby="cleanup-assistant-title" className="space-y-5 pb-24">
      <div className="space-y-1">
        <h2 id="cleanup-assistant-title" className="text-lg font-semibold tracking-tight">
          {t("cleanup.title")}
        </h2>
        <p className="text-muted-foreground text-sm">{t("cleanup.description")}</p>
      </div>

      {/* En tête : tant que ces données manquent, scores et protections sont faux. */}
      {data && data.unreliable_sources.length > 0 && (
        <Alert variant="destructive">
          <TriangleAlert className="size-4" />
          <AlertTitle>{t("cleanup.unreliable.title")}</AlertTitle>
          <AlertDescription className="space-y-1">
            {data.unreliable_sources.map((source) => (
              <p key={source}>{t(`cleanup.unreliable.${source}`)}</p>
            ))}
            <p>{t("cleanup.unreliable.advice")}</p>
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
          <Input
            type="search"
            aria-label={t("cleanup.search")}
            placeholder={t("cleanup.search")}
            className={cn("w-56 pl-8 [&::-webkit-search-cancel-button]:hidden", filters.search && "pr-8")}
            maxLength={100}
            value={filters.search}
            onChange={(e) => update({ search: e.target.value })}
          />
          {filters.search && (
            <button
              type="button"
              onClick={() => update({ search: "" })}
              aria-label={t("filters.clearSearch")}
              className="text-muted-foreground hover:text-foreground absolute top-1/2 right-2 -translate-y-1/2"
            >
              <X className="size-4" />
            </button>
          )}
        </div>
        <Select
          value={filters.type ?? "all"}
          onValueChange={(value) => update({ type: value === "movie" || value === "series" ? value : undefined })}
        >
          <SelectTrigger className="w-40" aria-label={typeLabel("all")}>
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
        <Select value={filters.sort} onValueChange={(value) => update({ sort: value as CleanupSort })}>
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
        <div className="flex items-center gap-2 px-1">
          <Switch
            id="cleanup-show-protected"
            checked={filters.protected}
            onCheckedChange={(checked) => update({ protected: checked })}
          />
          <Label htmlFor="cleanup-show-protected" className="font-normal">
            {t("cleanup.showProtected")}
          </Label>
        </div>
        {isFetching && !isLoading && <Loader2 className="text-muted-foreground size-4 animate-spin" />}
        <div className="ml-auto flex items-center gap-2">
          <Button
            type="button"
            variant={settingsOpen ? "secondary" : "outline"}
            aria-expanded={settingsOpen}
            aria-controls={SETTINGS_PANEL_ID}
            onClick={() => setSettingsOpen((open) => !open)}
          >
            <SlidersHorizontal className="size-4" />
            {t("cleanup.settings.title")}
          </Button>
          <GridSizeToggle value={gridSize} onChange={setGridSize} />
        </div>
      </div>

      {settingsOpen && <CleanupSettingsPanel id={SETTINGS_PANEL_ID} />}

      {data && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm" role="status">
            <span className="font-medium">
              {t("cleanup.summary", { count: data.candidate_count, size: formatBytes(data.total_reclaimable_bytes) })}
            </span>
            {data.protected_count > 0 && (
              <span className="text-muted-foreground"> · {t("cleanup.protectedCount", { count: data.protected_count })}</span>
            )}
          </p>
          {selectable.length > 0 && (
            <Button type="button" variant="ghost" size="sm" onClick={togglePage}>
              {pageSelected ? t("cleanup.unselectPage") : t("cleanup.selectPage")}
            </Button>
          )}
        </div>
      )}

      {isLoading && (
        <MediaGrid size={gridSize}>
          {Array.from({ length: 12 }).map((_, i) => (
            <Skeleton key={i} className="aspect-2/3 w-full" />
          ))}
        </MediaGrid>
      )}
      {isError && <p className="text-destructive text-sm">{t("cleanup.loadFailed")}</p>}
      {data && data.items.length === 0 && (
        <div className="text-muted-foreground space-y-1 py-12 text-center text-sm">
          <p>{t("cleanup.empty")}</p>
          <p>{t("cleanup.emptyHint")}</p>
        </div>
      )}
      {data && data.items.length > 0 && (
        <MediaGrid size={gridSize}>
          {data.items.map((candidate) => (
            <CandidateCard
              key={candidate.media_id}
              candidate={candidate}
              selected={selected.has(candidate.media_id)}
              onToggle={toggle}
            />
          ))}
        </MediaGrid>
      )}

      {data && pages > 1 && (
        <Pagination
          page={filters.page}
          pages={pages}
          onPage={(page) => {
            update({ page })
            window.scrollTo({ top: 0 })
          }}
        />
      )}

      {selected.size > 0 && (
        <div className="bg-background/95 border-border fixed inset-x-0 bottom-0 z-40 border-t backdrop-blur">
          <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
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
    </section>
  )
}
