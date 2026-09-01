import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

interface PathsCardProps {
  embyLibraryPath: string
  onEmbyLibraryPathChange: (value: string) => void
  qbittorrentDownloadPath: string
  onQbittorrentDownloadPathChange: (value: string) => void
}

export function PathsCard({
  embyLibraryPath,
  onEmbyLibraryPathChange,
  qbittorrentDownloadPath,
  onQbittorrentDownloadPathChange,
}: PathsCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Chemins des dossiers</CardTitle>
        <CardDescription>
          Ces chemins servent à détecter les hardlinks entre les téléchargements et la bibliothèque. Ils doivent
          être saisis tels que vus <strong>depuis le conteneur Analysarr</strong>, après montage des volumes — pas
          le chemin sur la machine hôte.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="emby-library-path">Dossier de la bibliothèque Emby</Label>
          <Input
            id="emby-library-path"
            placeholder="/data/media"
            value={embyLibraryPath}
            onChange={(e) => onEmbyLibraryPathChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">
            Le point de montage du volume de bibliothèque dans le conteneur Analysarr.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="qbit-download-path">Dossier de téléchargement qBittorrent</Label>
          <Input
            id="qbit-download-path"
            placeholder="/data/downloads"
            value={qbittorrentDownloadPath}
            onChange={(e) => onQbittorrentDownloadPathChange(e.target.value)}
            autoComplete="off"
          />
          <p className="text-muted-foreground text-sm">
            Le point de montage du volume de téléchargement dans le conteneur Analysarr.
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
