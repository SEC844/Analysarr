import { PathBrowserButton } from "@/components/settings/path-browser-dialog"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useI18n } from "@/i18n"

interface PathsCardProps {
  // Nom du client torrent configuré : le dossier de téléchargement n'est pas
  // toujours celui de qBittorrent.
  torrentClientName: string
  embyLibraryPath: string
  onEmbyLibraryPathChange: (value: string) => void
  qbittorrentDownloadPath: string
  onQbittorrentDownloadPathChange: (value: string) => void
}

export function PathsCard({
  torrentClientName,
  embyLibraryPath,
  onEmbyLibraryPathChange,
  qbittorrentDownloadPath,
  onQbittorrentDownloadPathChange,
}: PathsCardProps) {
  const { t, rich } = useI18n()

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("paths.title")}</CardTitle>
        <CardDescription>
          {rich("paths.description", { emphasis: <strong>{t("paths.fromContainer")}</strong> })}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="emby-library-path">{t("paths.embyLibrary")}</Label>
          <div className="flex gap-2">
            <Input
              id="emby-library-path"
              placeholder="/data/media"
              value={embyLibraryPath}
              onChange={(e) => onEmbyLibraryPathChange(e.target.value)}
              autoComplete="off"
            />
            <PathBrowserButton value={embyLibraryPath} onSelect={onEmbyLibraryPathChange} />
          </div>
          <p className="text-muted-foreground text-sm">{t("paths.embyLibraryHelp")}</p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="qbit-download-path">{t("paths.torrentDownload", { client: torrentClientName })}</Label>
          <div className="flex gap-2">
            <Input
              id="qbit-download-path"
              placeholder="/data/downloads"
              value={qbittorrentDownloadPath}
              onChange={(e) => onQbittorrentDownloadPathChange(e.target.value)}
              autoComplete="off"
            />
            <PathBrowserButton value={qbittorrentDownloadPath} onSelect={onQbittorrentDownloadPathChange} />
          </div>
          <p className="text-muted-foreground text-sm">{t("paths.torrentDownloadHelp", { client: torrentClientName })}</p>
        </div>
      </CardContent>
    </Card>
  )
}
