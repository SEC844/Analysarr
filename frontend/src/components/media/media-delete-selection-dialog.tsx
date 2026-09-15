import { useMemo, useState, type ReactNode } from "react"
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Clapperboard,
  HardDriveDownload,
  Loader2,
  Trash2,
  Tv,
  XCircle,
} from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { watchProgressLabel } from "@/components/media/watch-stats"
import { usePreferences } from "@/hooks/use-app"
import { useDeleteFootprintQuery, useDeleteSelectionExecuteMutation, useMediaWatchQuery } from "@/hooks/use-media"
import { useI18n } from "@/i18n"
import { diskBytes, fileKey, indexFootprint, reclaimedBytes, torrentKey } from "@/lib/footprint"
import { formatBytes } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { MediaDeleteSelectionResult, MediaDetail, MediaFileRead } from "@/types/media"

interface TreeNode {
  key: string
  label: string
  title?: string
  icon?: ReactNode
  size: number
  leafKeys: string[]
  children: TreeNode[]
}

function leaf(key: string, label: string, size: number | null, title?: string): TreeNode {
  return { key, label, title, size: size ?? 0, leafKeys: [key], children: [] }
}

function group(key: string, label: string, children: TreeNode[]): TreeNode {
  return {
    key,
    label,
    size: children.reduce((sum, c) => sum + c.size, 0),
    leafKeys: children.flatMap((c) => c.leafKeys),
    children,
  }
}

type Translate = ReturnType<typeof useI18n>["t"]

function groupBySeason(files: MediaFileRead[], t: Translate): [string, MediaFileRead[]][] {
  // "~" trie les fichiers sans épisode identifié après toutes les saisons.
  const sorted = [...files].sort((a, b) => (a.episode_label ?? "~").localeCompare(b.episode_label ?? "~"))
  const groups = new Map<string, MediaFileRead[]>()
  for (const f of sorted) {
    const season = f.episode_label?.match(/^S(\d+)/)?.[1]
    const key = season ? t("media.season", { number: parseInt(season, 10) }) : t("media.otherFiles")
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(f)
  }
  return [...groups.entries()]
}

// Un seul élément dans une section : ligne simple, sans niveau d'arbre.
function section(key: string, label: string, icon: ReactNode, children: TreeNode[]): TreeNode | null {
  if (children.length === 0) return null
  const node = children.length === 1 && children[0].children.length === 0 ? children[0] : group(key, label, children)
  return { ...node, icon }
}

function buildTree(media: MediaDetail, t: Translate): TreeNode[] {
  const isSeries = media.media_type === "series"
  const fileLeaf = (f: MediaFileRead) => {
    const name = f.path.split(/[\\/]/).pop() || f.path
    return leaf(fileKey(f.id), isSeries ? (f.episode_label ?? name) : name, f.size, f.path)
  }

  const torrents = section(
    "torrents",
    t("deleteSelection.torrents"),
    <HardDriveDownload className="text-muted-foreground size-4 shrink-0" />,
    media.torrents.map((torrent) => leaf(torrentKey(torrent.id), torrent.name, torrent.size)),
  )
  const library = section(
    "library",
    t("deleteSelection.library"),
    isSeries ? (
      <Tv className="text-muted-foreground size-4 shrink-0" />
    ) : (
      <Clapperboard className="text-muted-foreground size-4 shrink-0" />
    ),
    isSeries && media.files.length > 1
      ? groupBySeason(media.files, t).map(([season, files]) => group(`season:${season}`, season, files.map(fileLeaf)))
      : media.files.map(fileLeaf),
  )
  return [torrents, library].filter((n): n is TreeNode => n !== null)
}

interface TreeState {
  selected: Set<string>
  expanded: Set<string>
  sizeOf: (node: TreeNode) => number
  toggleSelect: (keys: string[], checked: boolean) => void
  toggleExpand: (key: string) => void
}

function TreeRow({ node, state }: { node: TreeNode; state: TreeState }) {
  const { t } = useI18n()
  const selectedCount = node.leafKeys.filter((k) => state.selected.has(k)).length
  const checked = selectedCount === node.leafKeys.length
  const isGroup = node.children.length > 0
  const isOpen = state.expanded.has(node.key)

  return (
    <li>
      <div className="hover:bg-muted/50 flex items-center gap-2 rounded-md px-1 py-1.5">
        {isGroup ? (
          <button
            type="button"
            onClick={() => state.toggleExpand(node.key)}
            aria-expanded={isOpen}
            aria-label={t(isOpen ? "deleteSelection.collapse" : "deleteSelection.expand", { name: node.label })}
            className="text-muted-foreground hover:text-foreground shrink-0"
          >
            <ChevronRight className={cn("size-4 transition-transform", isOpen && "rotate-90")} />
          </button>
        ) : (
          <span className="size-4 shrink-0" aria-hidden />
        )}
        <Checkbox
          checked={checked}
          indeterminate={selectedCount > 0 && !checked}
          onCheckedChange={(next) => state.toggleSelect(node.leafKeys, next)}
          aria-label={node.label}
        />
        {node.icon}
        <button
          type="button"
          title={node.title ?? node.label}
          onClick={() => (isGroup ? state.toggleExpand(node.key) : state.toggleSelect(node.leafKeys, !checked))}
          className="min-w-0 flex-1 truncate text-left"
        >
          {node.label}
          {isGroup && <span className="text-muted-foreground"> ({node.leafKeys.length})</span>}
        </button>
        <span className="text-muted-foreground shrink-0 text-xs">{formatBytes(state.sizeOf(node))}</span>
      </div>
      {isGroup && isOpen && (
        <ul className="border-border ml-3 border-l pl-2">
          {node.children.map((child) => (
            <TreeRow key={child.key} node={child} state={state} />
          ))}
        </ul>
      )}
    </li>
  )
}

export function MediaDeleteSelectionDialog({ media, onMediaDeleted }: { media: MediaDetail; onMediaDeleted: () => void }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const prefs = usePreferences()
  const [removeFromArr, setRemoveFromArr] = useState(false)
  const [removeFromSeer, setRemoveFromSeer] = useState(false)
  const [result, setResult] = useState<MediaDeleteSelectionResult | null>(null)

  const executeMutation = useDeleteSelectionExecuteMutation()
  const footprintQuery = useDeleteFootprintQuery(media.id, open && !result)
  const watchQuery = useMediaWatchQuery(media.id, open && !result)
  const watchStats = watchQuery.data
  const watchingUsers = watchStats?.users.filter((u) => u.in_progress) ?? []

  const tree = useMemo(() => buildTree(media, t), [media, t])
  const allKeys = useMemo(() => tree.flatMap((n) => n.leafKeys), [tree])

  const footprint = footprintQuery.data
  const unitsByKey = useMemo(() => (footprint ? indexFootprint(footprint) : null), [footprint])

  const isSeries = media.media_type === "series"
  const selectedTorrents = media.torrents.filter((torrent) => selected.has(torrentKey(torrent.id)))
  const selectedFiles = media.files.filter((f) => selected.has(fileKey(f.id)))
  const hasSelection = selected.size > 0
  const allSelected = allKeys.length > 0 && allKeys.every((k) => selected.has(k))
  // Retrait du média entier de Sonarr/Radarr : seulement si toute sa
  // bibliothèque est cochée, sinon des fichiers non cochés seraient supprimés.
  const wholeLibrary = hasSelection && selectedFiles.length === media.files.length
  const arrId = isSeries ? media.sonarr_id : media.radarr_id
  const canRemoveMedia = wholeLibrary && arrId !== null
  const showArrOption = canRemoveMedia || (isSeries && selectedFiles.length > 0)
  // Seer : même règle que le retrait du média entier de Sonarr/Radarr.
  const showSeerOption = wholeLibrary && media.requests.length > 0

  // Sans empreinte disque (chargement, erreur) : repli sur la somme des tailles.
  const nominalBytes =
    selectedTorrents.reduce((sum, torrent) => sum + (torrent.size ?? 0), 0) +
    selectedFiles.reduce((sum, f) => sum + (f.size ?? 0), 0)
  const selectedBytes = footprint && unitsByKey ? diskBytes(footprint, unitsByKey, selected) : nominalBytes
  const reclaimed = footprint && unitsByKey ? reclaimedBytes(footprint, unitsByKey, selected) : null

  const treeState: TreeState = {
    selected,
    expanded,
    sizeOf: (node) => (footprint && unitsByKey ? diskBytes(footprint, unitsByKey, node.leafKeys) : node.size),
    toggleSelect: (keys, checked) =>
      setSelected((prev) => {
        const next = new Set(prev)
        for (const key of keys) {
          if (checked) next.add(key)
          else next.delete(key)
        }
        return next
      }),
    toggleExpand: (key) =>
      setExpanded((prev) => {
        const next = new Set(prev)
        if (!next.delete(key)) next.add(key)
        return next
      }),
  }

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (next) {
      // Cases de retrait cochées d'office si la préférence le demande.
      setRemoveFromArr(prefs.delete_remove_from_arr_default)
      setRemoveFromSeer(prefs.delete_remove_from_arr_default)
    } else {
      setSelected(new Set())
      setExpanded(new Set())
      setResult(null)
    }
  }

  function handleSelectAll() {
    if (allSelected) {
      setSelected(new Set())
      setRemoveFromArr(prefs.delete_remove_from_arr_default)
      setRemoveFromSeer(prefs.delete_remove_from_arr_default)
    } else {
      setSelected(new Set(allKeys))
      setRemoveFromArr(true)
      setRemoveFromSeer(true)
    }
  }

  const arrLabel = t(
    !canRemoveMedia
      ? "deleteSelection.unmonitorEpisodes"
      : isSeries
        ? "deleteSelection.removeSeries"
        : "deleteSelection.removeMovie",
  )

  const handleConfirm = () => {
    executeMutation.mutate(
      {
        id: media.id,
        selection: {
          torrent_ids: selectedTorrents.map((torrent) => torrent.id),
          media_file_ids: selectedFiles.map((f) => f.id),
          remove_from_arr: removeFromArr && showArrOption,
          remove_from_seer: removeFromSeer && showSeerOption,
        },
      },
      {
        onSuccess: (data) => {
          const failedCount = data.steps.filter((s) => !s.success).length
          if (failedCount === 0) toast.success(t("deleteSelection.success"))
          else toast.error(t("deleteSelection.partialFailure", { failed: failedCount, total: data.steps.length }))

          if (data.media_deleted) {
            // Plus rien ne subsiste pour ce média : la fiche elle-même a
            // disparu côté serveur, inutile d'afficher un récapitulatif
            // pour une page qui n'existe plus — retour direct.
            setOpen(false)
            onMediaDeleted()
            return
          }
          setResult(data)
        },
        onError: (err) => toast.error(err instanceof Error ? err.message : t("deleteSelection.failed")),
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger
        render={<Button type="button" variant="outline" size="icon" title={t("deleteSelection.trigger")} />}
      >
        <Trash2 className="size-4" />
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("deleteSelection.title")}</DialogTitle>
          <DialogDescription>{t("deleteSelection.description")}</DialogDescription>
        </DialogHeader>

        {!result && (
          // min-w-0 : élément de la grille du dialogue, sinon sa largeur minimale
          // est celle du plus long nom de fichier et la troncature ne s'applique
          // jamais — la liste déborde du dialogue et masque les tailles.
          <div className="min-w-0 space-y-3">
            {allKeys.length > 1 && (
              <div className="flex items-center justify-between gap-2">
                <p className="text-muted-foreground text-sm">
                  {t("deleteSelection.selectedCount", { selected: selected.size, total: allKeys.length })}
                </p>
                <Button type="button" variant="outline" size="sm" onClick={handleSelectAll}>
                  {allSelected ? (
                    t("deleteSelection.deselectAll")
                  ) : (
                    <>
                      <Trash2 className="size-4" />
                      {t("deleteSelection.selectAll")}
                    </>
                  )}
                </Button>
              </div>
            )}

            <ul className="max-h-80 overflow-y-auto text-sm">
              {tree.map((node) => (
                <TreeRow key={node.key} node={node} state={treeState} />
              ))}
            </ul>

            {selectedFiles.length > 0 && watchStats && watchingUsers.length > 0 && (
              // Supprimer la bibliothèque couperait la lecture en cours de ces
              // utilisateurs : avertissement avant confirmation.
              <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-300">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                <span>
                  {t("watch.deleteWarning", {
                    count: watchingUsers.length,
                    list: watchingUsers.map((u) => `${u.name} (${watchProgressLabel(u, watchStats, t)})`).join(", "),
                  })}
                </span>
              </div>
            )}

            {showArrOption && (
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={removeFromArr} onCheckedChange={setRemoveFromArr} />
                {arrLabel}
              </label>
            )}

            {showSeerOption && (
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={removeFromSeer} onCheckedChange={setRemoveFromSeer} />
                {t("seer.deleteOption")}
              </label>
            )}

            {hasSelection && (
              <div className="bg-muted/50 rounded-md px-3 py-2 text-sm">
                <p className="flex items-center gap-2 font-medium">
                  {t("deleteSelection.reclaimed")}{" "}
                  {footprintQuery.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    formatBytes(reclaimed ?? selectedBytes)
                  )}
                </p>
                {reclaimed !== null && reclaimed < selectedBytes && (
                  <p className="text-muted-foreground text-xs">
                    {t("deleteSelection.selectionHint", { size: formatBytes(selectedBytes) })}
                  </p>
                )}
                {footprintQuery.isError && (
                  <p className="text-muted-foreground text-xs">{t("deleteSelection.estimate")}</p>
                )}
              </div>
            )}
          </div>
        )}

        {result && (
          <ul className="max-h-96 space-y-1 overflow-y-auto text-sm">
            {result.steps.map((step, i) => (
              <li key={i} className="flex items-start gap-2 py-1">
                {step.success ? (
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-500" />
                ) : (
                  <XCircle className="text-destructive mt-0.5 size-4 shrink-0" />
                )}
                <div>
                  <p className="break-all">{step.label}</p>
                  {step.error && <p className="text-destructive text-xs">{step.error}</p>}
                </div>
              </li>
            ))}
          </ul>
        )}

        <DialogFooter>
          {!result && (
            <Button
              type="button"
              variant="destructive"
              disabled={!hasSelection || executeMutation.isPending}
              onClick={handleConfirm}
            >
              {executeMutation.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Trash2 className="size-4" />
              )}
              {t("common.confirmDelete")}
            </Button>
          )}
          <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
            {t("common.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
