import { useMemo, useState, type ReactNode } from "react"
import { CheckCircle2, ChevronRight, Clapperboard, HardDriveDownload, Loader2, Trash2, Tv, XCircle } from "lucide-react"
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
import { useDeleteFootprintQuery, useDeleteSelectionExecuteMutation } from "@/hooks/use-media"
import { formatBytes } from "@/lib/format"
import { cn } from "@/lib/utils"
import type {
  DeleteFootprintItem,
  MediaDeleteFootprint,
  MediaDeleteSelectionResult,
  MediaDetail,
  MediaFileRead,
} from "@/types/media"

// Torrents et fichiers de bibliothèque partagent un seul ensemble de
// sélection : clés préfixées pour les distinguer au moment de l'envoi.
const torrentKey = (id: number) => `t:${id}`
const fileKey = (id: number) => `f:${id}`

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

function seasonLabel(episodeLabel: string | null): string {
  const match = episodeLabel?.match(/^S(\d+)/)
  return match ? `Saison ${parseInt(match[1], 10)}` : "Autres fichiers"
}

function groupBySeason(files: MediaFileRead[]): [string, MediaFileRead[]][] {
  // "~" trie les fichiers sans épisode identifié après toutes les saisons.
  const sorted = [...files].sort((a, b) => (a.episode_label ?? "~").localeCompare(b.episode_label ?? "~"))
  const groups = new Map<string, MediaFileRead[]>()
  for (const f of sorted) {
    const key = seasonLabel(f.episode_label)
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

function buildTree(media: MediaDetail): TreeNode[] {
  const isSeries = media.media_type === "series"
  const fileLeaf = (f: MediaFileRead) => {
    const name = f.path.split(/[\\/]/).pop() || f.path
    return leaf(fileKey(f.id), isSeries ? (f.episode_label ?? name) : name, f.size, f.path)
  }

  const torrents = section(
    "torrents",
    "Torrents",
    <HardDriveDownload className="text-muted-foreground size-4 shrink-0" />,
    media.torrents.map((t) => leaf(torrentKey(t.id), t.name, t.size)),
  )
  const library = section(
    "library",
    "Bibliothèque",
    isSeries ? (
      <Tv className="text-muted-foreground size-4 shrink-0" />
    ) : (
      <Clapperboard className="text-muted-foreground size-4 shrink-0" />
    ),
    isSeries && media.files.length > 1
      ? groupBySeason(media.files).map(([season, files]) => group(`season:${season}`, season, files.map(fileLeaf)))
      : media.files.map(fileLeaf),
  )
  return [torrents, library].filter((n): n is TreeNode => n !== null)
}

// Unités disque (inodes) de chaque élément, indexées par clé de sélection.
function indexFootprint(footprint: MediaDeleteFootprint): Map<string, number[]> {
  const index = (items: DeleteFootprintItem[], key: (id: number) => string) =>
    items.map((item): [string, number[]] => [key(item.id), item.units])
  return new Map([...index(footprint.torrents, torrentKey), ...index(footprint.files, fileKey)])
}

// Taille réelle sur disque d'un ensemble d'éléments : chaque inode compté
// une seule fois (3 torrents hardlinkés d'un même film = une seule taille).
function diskBytes(footprint: MediaDeleteFootprint, unitsByKey: Map<string, number[]>, keys: Iterable<string>): number {
  const units = new Set<number>()
  for (const key of keys) for (const unit of unitsByKey.get(key) ?? []) units.add(unit)
  let total = 0
  for (const unit of units) total += footprint.units[unit].size
  return total
}

// Espace RÉELLEMENT libéré : un inode n'est libéré que si tous ses liens
// sont sélectionnés — 100 hardlinks d'un même fichier ne libèrent qu'une
// seule fois sa taille, et rien du tout si l'un d'eux est conservé.
function reclaimedBytes(footprint: MediaDeleteFootprint, unitsByKey: Map<string, number[]>, keys: Iterable<string>): number {
  const selectedLinks = new Map<number, number>()
  for (const key of keys) {
    for (const unit of unitsByKey.get(key) ?? []) selectedLinks.set(unit, (selectedLinks.get(unit) ?? 0) + 1)
  }
  let total = 0
  for (const [unit, links] of selectedLinks) {
    if (links >= footprint.units[unit].links) total += footprint.units[unit].size
  }
  return total
}

interface TreeState {
  selected: Set<string>
  expanded: Set<string>
  sizeOf: (node: TreeNode) => number
  toggleSelect: (keys: string[], checked: boolean) => void
  toggleExpand: (key: string) => void
}

function TreeRow({ node, state }: { node: TreeNode; state: TreeState }) {
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
            aria-label={isOpen ? `Replier ${node.label}` : `Déplier ${node.label}`}
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
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [removeFromArr, setRemoveFromArr] = useState(false)
  const [result, setResult] = useState<MediaDeleteSelectionResult | null>(null)

  const executeMutation = useDeleteSelectionExecuteMutation()
  const footprintQuery = useDeleteFootprintQuery(media.id, open && !result)

  const tree = useMemo(() => buildTree(media), [media])
  const allKeys = useMemo(() => tree.flatMap((n) => n.leafKeys), [tree])

  const footprint = footprintQuery.data
  const unitsByKey = useMemo(() => (footprint ? indexFootprint(footprint) : null), [footprint])

  const isSeries = media.media_type === "series"
  const selectedTorrents = media.torrents.filter((t) => selected.has(torrentKey(t.id)))
  const selectedFiles = media.files.filter((f) => selected.has(fileKey(f.id)))
  const hasSelection = selected.size > 0
  const allSelected = allKeys.length > 0 && allKeys.every((k) => selected.has(k))
  // Retrait du média entier de Sonarr/Radarr : seulement si toute sa
  // bibliothèque est cochée, sinon des fichiers non cochés seraient supprimés.
  const wholeLibrary = hasSelection && selectedFiles.length === media.files.length
  const arrId = isSeries ? media.sonarr_id : media.radarr_id
  const canRemoveMedia = wholeLibrary && arrId !== null
  const showArrOption = canRemoveMedia || (isSeries && selectedFiles.length > 0)

  // Sans empreinte disque (chargement, erreur) : repli sur la somme des tailles.
  const nominalBytes =
    selectedTorrents.reduce((sum, t) => sum + (t.size ?? 0), 0) + selectedFiles.reduce((sum, f) => sum + (f.size ?? 0), 0)
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
    if (!next) {
      setSelected(new Set())
      setExpanded(new Set())
      setRemoveFromArr(false)
      setResult(null)
    }
  }

  function handleSelectAll() {
    if (allSelected) {
      setSelected(new Set())
      setRemoveFromArr(false)
    } else {
      setSelected(new Set(allKeys))
      setRemoveFromArr(true)
    }
  }

  const arrLabel = !canRemoveMedia
    ? "Démonitorer aussi ces épisodes dans Sonarr (empêche un retéléchargement automatique)"
    : isSeries
      ? "Retirer aussi la série de Sonarr (sans l'ajouter à la liste d'exclusion)"
      : "Retirer aussi le film de Radarr (sans l'ajouter à la liste d'exclusion)"

  const handleConfirm = () => {
    executeMutation.mutate(
      {
        id: media.id,
        selection: {
          torrent_ids: selectedTorrents.map((t) => t.id),
          media_file_ids: selectedFiles.map((f) => f.id),
          remove_from_arr: removeFromArr && showArrOption,
        },
      },
      {
        onSuccess: (data) => {
          const failedCount = data.steps.filter((s) => !s.success).length
          if (failedCount === 0) toast.success("Suppression effectuée.")
          else toast.error(`${failedCount} échec(s) sur ${data.steps.length}.`)

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
        onError: (err) => toast.error(err instanceof Error ? err.message : "Échec de la suppression."),
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger render={<Button type="button" variant="outline" size="icon" title="Supprimer..." />}>
        <Trash2 className="size-4" />
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Supprimer</DialogTitle>
          <DialogDescription>
            Cochez ce qu'il faut supprimer : cocher un groupe coche tout son contenu. Rien n'est supprimé avant
            confirmation.
          </DialogDescription>
        </DialogHeader>

        {!result && (
          <div className="space-y-3">
            {allKeys.length > 1 && (
              <div className="flex items-center justify-between gap-2">
                <p className="text-muted-foreground text-sm">
                  {selected.size} élément(s) sélectionné(s) sur {allKeys.length}
                </p>
                <Button type="button" variant="outline" size="sm" onClick={handleSelectAll}>
                  {allSelected ? (
                    "Tout désélectionner"
                  ) : (
                    <>
                      <Trash2 className="size-4" />
                      Tout supprimer
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

            {showArrOption && (
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={removeFromArr} onCheckedChange={setRemoveFromArr} />
                {arrLabel}
              </label>
            )}

            {hasSelection && (
              <div className="bg-muted/50 rounded-md px-3 py-2 text-sm">
                <p className="flex items-center gap-2 font-medium">
                  Espace libéré :{" "}
                  {footprintQuery.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    formatBytes(reclaimed ?? selectedBytes)
                  )}
                </p>
                {reclaimed !== null && reclaimed < selectedBytes && (
                  <p className="text-muted-foreground text-xs">
                    Sélection de {formatBytes(selectedBytes)} : un fichier hardlinké n'est libéré du disque que si tous
                    ses liens sont supprimés.
                  </p>
                )}
                {footprintQuery.isError && (
                  <p className="text-muted-foreground text-xs">
                    Estimation : espace réel indisponible, les fichiers hardlinkés peuvent être comptés plusieurs fois.
                  </p>
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
              Confirmer la suppression
            </Button>
          )}
          <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
            Fermer
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
