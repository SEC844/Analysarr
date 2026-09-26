import { useState } from "react"
import { CheckCircle2, Link2, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { MuteStatusButton } from "@/components/media/ignore-actions"
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
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { useArrLinkMutation, useArrLinkPreviewQuery } from "@/hooks/use-media"
import { useI18n, type MessageKey } from "@/i18n"
import type { ArrAvailability, ArrCandidate, ArrLinkPreview, ArrMonitor, MediaDetail } from "@/types/media"

function candidateLabel(candidate: ArrCandidate): string {
  const ids = [
    candidate.tvdb_id ? `TVDB ${candidate.tvdb_id}` : null,
    candidate.tmdb_id ? `TMDB ${candidate.tmdb_id}` : null,
    candidate.imdb_id ? `IMDb ${candidate.imdb_id}` : null,
  ].filter(Boolean)
  return `${candidate.title}${candidate.year ? ` (${candidate.year})` : ""} · ${ids.join(" · ")}`
}

function LinkForm({
  preview,
  candidateKey,
  folder,
  profileId,
  monitor,
  availability,
  onCandidate,
  onFolder,
  onProfile,
  onMonitor,
  onAvailability,
}: {
  preview: ArrLinkPreview
  candidateKey: string
  folder: string
  profileId: string
  monitor: ArrMonitor
  availability: ArrAvailability
  onCandidate: (value: string) => void
  onFolder: (value: string) => void
  onProfile: (value: string) => void
  onMonitor: (value: ArrMonitor) => void
  onAvailability: (value: ArrAvailability) => void
}) {
  const { t } = useI18n()
  const isSeries = preview.service === "sonarr"
  const monitors: ArrMonitor[] = isSeries ? ["existing", "all", "future", "none"] : ["existing", "none"]

  if (preview.candidates.length === 0) {
    return <p className="text-sm break-words">{t("linkArr.noCandidate")}</p>
  }

  return (
    // `min-w-0` : sans lui, un titre ou un chemin long élargirait la grille du
    // dialogue et déborderait au lieu d'être tronqué.
    <div className="min-w-0 space-y-4">
      <div className="min-w-0 space-y-1.5">
        <Label htmlFor="link-candidate">{t("linkArr.candidate")}</Label>
        <Select value={candidateKey} onValueChange={(value) => onCandidate(value ?? "")}>
          <SelectTrigger id="link-candidate" className="w-full">
            <SelectValue>
              {(value: string) => {
                const found = preview.candidates.find((candidate) => candidate.key === value)
                return <span className="truncate">{found ? candidateLabel(found) : value}</span>
              }}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {preview.candidates.map((candidate) => (
              <SelectItem key={candidate.key} value={candidate.key}>
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate">{candidateLabel(candidate)}</span>
                  {candidate.confidence === "certain" && (
                    <Badge variant="secondary" className="shrink-0">
                      <CheckCircle2 className="size-3" />
                      {t("linkArr.certain")}
                    </Badge>
                  )}
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-muted-foreground text-xs">{t("linkArr.candidateHelp")}</p>
      </div>

      <div className="min-w-0 space-y-1.5">
        <Label htmlFor="link-profile">{t("linkArr.qualityProfile")}</Label>
        <Select value={profileId} onValueChange={(value) => onProfile(value ?? "")}>
          <SelectTrigger id="link-profile" className="w-full">
            <SelectValue>
              {(value: string) =>
                preview.quality_profiles.find((profile) => String(profile.id) === value)?.name ?? value
              }
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {preview.quality_profiles.map((profile) => (
              <SelectItem key={profile.id} value={String(profile.id)}>
                {profile.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="min-w-0 space-y-1.5">
        <Label htmlFor="link-monitor">{t("linkArr.monitor")}</Label>
        <Select value={monitor} onValueChange={(value) => onMonitor((value ?? "existing") as ArrMonitor)}>
          <SelectTrigger id="link-monitor" className="w-full">
            <SelectValue>{(value: string) => t(`linkArr.monitors.${value}` as MessageKey)}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {monitors.map((value) => (
              <SelectItem key={value} value={value}>
                {t(`linkArr.monitors.${value}` as MessageKey)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Dossier à importer : uniquement ceux que Sonarr/Radarr déclare non
          rattachés, avec SES chemins — les nôtres peuvent différer. */}
      <div className="min-w-0 space-y-1.5">
        <Label htmlFor="link-folder">{t("linkArr.folder")}</Label>
        <Select value={folder} onValueChange={(value) => onFolder(value ?? "")}>
          <SelectTrigger id="link-folder" className="w-full">
            <SelectValue>
              {(value: string) => <span className="truncate">{value || t("linkArr.noFolder")}</span>}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {preview.folders.map((path) => (
              <SelectItem key={path} value={path}>
                <span className="truncate">{path}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-muted-foreground text-xs">{t("linkArr.folderHelp")}</p>
      </div>

      {!isSeries && (
        <div className="min-w-0 space-y-1.5">
          <Label htmlFor="link-availability">{t("linkArr.availability")}</Label>
          <Select value={availability} onValueChange={(value) => onAvailability((value ?? "released") as ArrAvailability)}>
            <SelectTrigger id="link-availability" className="w-full">
              <SelectValue>{(value: string) => t(`linkArr.availabilities.${value}` as MessageKey)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {(["announced", "inCinemas", "released"] as ArrAvailability[]).map((value) => (
                <SelectItem key={value} value={value}>
                  {t(`linkArr.availabilities.${value}` as MessageKey)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  )
}

/** Média présent dans la bibliothèque mais suivi par aucun Sonarr/Radarr :
 * Analysarr l'ajoute pour de bon, avec le dossier qui contient déjà ses
 * fichiers (repris tels quels, rien n'est retéléchargé). Chaque fiche
 * proposée a été résolue par Sonarr/Radarr puis confrontée au titre et à
 * l'année du média : les identifiants du serveur multimédia sont parfois faux
 * (voir services/arr_link.py). */
export function LinkToArrDialog({ media }: { media: MediaDetail }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  // Choix explicites de l'utilisateur ; vides tant qu'il n'a rien changé. Les
  // valeurs affichées sont dérivées du rendu (voir plus bas) plutôt que
  // recopiées dans un effet : Sonarr/Radarr répond APRÈS l'ouverture, et
  // recopier sa réponse dans un état relançait un rendu en cascade.
  const [candidateChoice, setCandidateChoice] = useState("")
  const [folderChoice, setFolderChoice] = useState("")
  const [profileChoice, setProfileChoice] = useState("")
  const [monitor, setMonitor] = useState<ArrMonitor>("none")
  const [availability, setAvailability] = useState<ArrAvailability>("released")

  // Recherche lancée à l'ouverture seulement : elle interroge Sonarr/Radarr.
  const preview = useArrLinkPreviewQuery(media.id, open)
  const linkMutation = useArrLinkMutation()
  const isSeries = media.media_type === "series"

  const candidateKey = candidateChoice || (preview.data?.candidates[0]?.key ?? "")
  const folder = folderChoice || preview.data?.suggested_folder || (preview.data?.folders[0] ?? "")
  const profileId =
    profileChoice || String(preview.data?.suggested_profile ?? preview.data?.quality_profiles[0]?.id ?? "")

  // Fermer le dialogue rend la main à ce que Sonarr/Radarr propose : une
  // réouverture ne doit pas rejouer une sélection faite pour une autre fiche.
  const handleOpenChange = (next: boolean) => {
    setOpen(next)
    if (!next) {
      setCandidateChoice("")
      setFolderChoice("")
      setProfileChoice("")
    }
  }

  const canLink = Boolean(candidateKey && profileId && folder)

  const handleLink = () =>
    linkMutation.mutate(
      {
        id: media.id,
        payload: {
          candidate_key: candidateKey,
          quality_profile_id: Number(profileId),
          folder,
          monitor,
          minimum_availability: availability,
        },
      },
      {
        onSuccess: (result) => {
          handleOpenChange(false)
          toast.success(t("linkArr.linked", { title: result.title }))
        },
        onError: (err) => toast.error(err instanceof Error ? err.message : t("linkArr.failed")),
      },
    )

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger
        render={
          <Button
            type="button"
            className="bg-amber-500 text-amber-950 hover:bg-amber-500/90 dark:bg-amber-400 dark:hover:bg-amber-400/90"
          />
        }
      >
        <Link2 className="size-4" />
        {t(isSeries ? "linkArr.buttonSeries" : "linkArr.buttonMovie")}
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t(isSeries ? "linkArr.buttonSeries" : "linkArr.buttonMovie")}</DialogTitle>
          <DialogDescription>{t(isSeries ? "linkArr.descriptionSeries" : "linkArr.descriptionMovie")}</DialogDescription>
        </DialogHeader>

        {preview.isPending && <Skeleton className="h-40 w-full" />}
        {preview.isError && (
          <p className="text-destructive text-sm break-words">
            {preview.error instanceof Error ? preview.error.message : t("linkArr.failed")}
          </p>
        )}
        {preview.data && (
          <LinkForm
            preview={preview.data}
            candidateKey={candidateKey}
            folder={folder}
            profileId={profileId}
            monitor={monitor}
            availability={availability}
            onCandidate={setCandidateChoice}
            onFolder={setFolderChoice}
            onProfile={setProfileChoice}
            onMonitor={setMonitor}
            onAvailability={setAvailability}
          />
        )}

        <DialogFooter>
          {/* Aucune fiche chez Sonarr/Radarr : le média ne pourra pas être
              rattaché, l'alerte peut être masquée d'ici même. */}
          {preview.data?.candidates.length === 0 && (
            <MuteStatusButton mediaId={media.id} status="manquant_arr" onDone={() => handleOpenChange(false)} />
          )}
          {(preview.data?.candidates.length ?? 0) > 0 && (
            <Button type="button" disabled={!canLink || linkMutation.isPending} onClick={handleLink}>
              {linkMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <Link2 className="size-4" />}
              {t("linkArr.confirm", { service: preview.data?.instance_name ?? "" })}
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
