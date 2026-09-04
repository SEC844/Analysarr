import { useEffect, useState } from "react"
import { ChevronRight, Folder, FolderOpen, Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { browseFilesystem } from "@/lib/api"
import type { BrowseResult } from "@/types/settings"

interface PathBrowserButtonProps {
  value: string
  onSelect: (path: string) => void
}

// Navigateur de dossiers façon Unraid, pour choisir un chemin en cliquant
// plutôt qu'en le tapant à l'aveugle — parcourt le système de fichiers du
// conteneur Analysarr lui-même (pas celui de la machine hôte).
export function PathBrowserButton({ value, onSelect }: PathBrowserButtonProps) {
  const [open, setOpen] = useState(false)
  const [result, setResult] = useState<BrowseResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = (path: string) => {
    setLoading(true)
    setError(null)
    browseFilesystem(path)
      .then(setResult)
      .catch((err) => setError(err instanceof Error ? err.message : "Impossible de lister ce dossier."))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (open) load(value || "/")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button type="button" variant="outline" size="sm" />}>
        <FolderOpen className="size-4" />
        Parcourir
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Choisir un dossier</DialogTitle>
          <DialogDescription>
            Navigation dans le système de fichiers du conteneur Analysarr — pas celui de la machine hôte.
          </DialogDescription>
        </DialogHeader>

        <p className="text-muted-foreground bg-muted/50 break-all rounded-md border px-2 py-1.5 font-mono text-xs">
          {result?.path ?? value ?? "/"}
        </p>

        {loading && (
          <div className="text-muted-foreground flex items-center gap-2 py-4 text-sm">
            <Loader2 className="size-4 animate-spin" /> Chargement...
          </div>
        )}

        {error && <p className="text-destructive text-sm">{error}</p>}

        {result && !loading && (
          <ul className="divide-border max-h-72 divide-y overflow-y-auto rounded-md border text-sm">
            {result.parent !== null && (
              <li>
                <button
                  type="button"
                  className="hover:bg-muted flex w-full items-center gap-2 px-3 py-2 text-left"
                  onClick={() => load(result.parent!)}
                >
                  .. (dossier parent)
                </button>
              </li>
            )}
            {result.directories.length === 0 && (
              <li className="text-muted-foreground px-3 py-2">Aucun sous-dossier.</li>
            )}
            {result.directories.map((d) => (
              <li key={d.path}>
                <button
                  type="button"
                  className="hover:bg-muted flex w-full items-center gap-2 px-3 py-2 text-left"
                  onClick={() => load(d.path)}
                >
                  <Folder className="text-muted-foreground size-4 shrink-0" />
                  <span className="truncate">{d.name}</span>
                  <ChevronRight className="text-muted-foreground ml-auto size-4 shrink-0" />
                </button>
              </li>
            ))}
          </ul>
        )}

        <DialogFooter>
          <Button
            type="button"
            disabled={!result}
            onClick={() => {
              if (result) {
                onSelect(result.path)
                setOpen(false)
              }
            }}
          >
            Sélectionner ce dossier
          </Button>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            Annuler
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
