import { useMutation } from "@tanstack/react-query"
import { CheckCircle2, Loader2, Search, XCircle } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { getPathDiagnostics } from "@/lib/api"
import type { PathDiagnostics } from "@/types/diagnostics"

function DiagnosticsBlock({ title, diag }: { title: string; diag: PathDiagnostics }) {
  const allResolved = diag.total > 0 && diag.resolved === diag.total
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        {diag.total === 0 ? (
          <span className="text-muted-foreground">{title} : aucun élément</span>
        ) : allResolved ? (
          <>
            <CheckCircle2 className="size-4 text-emerald-500" />
            {title} : {diag.resolved}/{diag.total} chemins accessibles
          </>
        ) : (
          <>
            <XCircle className="text-destructive size-4" />
            {title} : {diag.resolved}/{diag.total} chemins accessibles
          </>
        )}
      </div>
      {diag.unresolved_samples.length > 0 && (
        <ul className="border-border max-h-48 space-y-1 overflow-y-auto rounded-md border p-2 text-xs">
          {diag.unresolved_samples.map((c, i) => (
            <li key={i} className="text-muted-foreground">
              <span className="text-foreground">{c.label}</span>
              <br />
              <span className="break-all">{c.path ?? "(chemin vide)"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function PathDiagnosticsPanel() {
  const diagnostics = useMutation({ mutationFn: getPathDiagnostics })

  return (
    <Card>
      <CardHeader>
        <CardTitle>Diagnostic des chemins</CardTitle>
        <CardDescription>
          Vérifie, en direct, si les chemins renvoyés par Emby et qBittorrent sont réellement accessibles depuis le
          conteneur Analysarr. Un chemin inaccessible signifie que le point de montage ne correspond pas à celui
          utilisé par Emby ou qBittorrent — la détection de doublons/orphelins ne peut pas fonctionner pour ces
          fichiers tant que ce n'est pas corrigé dans la configuration Docker.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Button type="button" variant="secondary" disabled={diagnostics.isPending} onClick={() => diagnostics.mutate()}>
          {diagnostics.isPending ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
          Lancer le diagnostic
        </Button>

        {diagnostics.isError && (
          <p className="text-destructive text-sm">
            {diagnostics.error instanceof Error ? diagnostics.error.message : "Échec du diagnostic."}
          </p>
        )}

        {diagnostics.data && (
          <div className="space-y-4">
            <DiagnosticsBlock title="qBittorrent" diag={diagnostics.data.qbittorrent} />
            <DiagnosticsBlock title="Emby (films)" diag={diagnostics.data.emby} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
