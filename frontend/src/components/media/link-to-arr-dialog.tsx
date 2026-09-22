import { useEffect, useState } from "react"
import { CheckCircle2, Link2, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
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
import { useI18n } from "@/i18n"
import type { ArrCandidate, ArrLinkPreview, MediaDetail } from "@/types/media"

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
  rootFolder,
  profileId,
  onCandidate,
  onRootFolder,
  onProfile,
}: {
  preview: ArrLinkPreview
  candidateKey: string
  rootFolder: string
  profileId: string
  onCandidate: (value: string) => void
  onRootFolder: (value: string) => void
  onProfile: (value: string) => void
}) {
  const { t } = useI18n()

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
        <Label htmlFor="link-root">{t("linkArr.rootFolder")}</Label>
        <Select value={rootFolder} onValueChange={(value) => onRootFolder(value ?? "")}>
          <SelectTrigger id="link-root" className="w-full">
            <SelectValue>
              {(value: string) => <span className="truncate">{value || t("linkArr.noRootFolder")}</span>}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {preview.root_folders.map((folder) => (
              <SelectItem key={folder} value={folder}>
                <span className="truncate">{folder}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-muted-foreground text-xs">{t("linkArr.rootFolderHelp")}</p>
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
  const [candidateKey, setCandidateKey] = useState("")
  const [rootFolder, setRootFolder] = useState("")
  const [profileId, setProfileId] = useState("")

  // Recherche lancée à l'ouverture seulement : elle interroge Sonarr/Radarr.
  const preview = useArrLinkPreviewQuery(media.id, open)
  const linkMutation = useArrLinkMutation()
  const isSeries = media.media_type === "series"

  useEffect(() => {
    if (!preview.data) return
    setCandidateKey(preview.data.candidates[0]?.key ?? "")
    setRootFolder(preview.data.suggested_root ?? preview.data.root_folders[0] ?? "")
    setProfileId(String(preview.data.suggested_profile ?? preview.data.quality_profiles[0]?.id ?? ""))
  }, [preview.data])

  const canLink = Boolean(candidateKey && rootFolder && profileId)

  const handleLink = () =>
    linkMutation.mutate(
      {
        id: media.id,
        payload: { candidate_key: candidateKey, root_folder: rootFolder, quality_profile_id: Number(profileId) },
      },
      {
        onSuccess: (result) => {
          setOpen(false)
          toast.success(t("linkArr.linked", { title: result.title }))
        },
        onError: (err) => toast.error(err instanceof Error ? err.message : t("linkArr.failed")),
      },
    )

  return (
    <Dialog open={open} onOpenChange={setOpen}>
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
            rootFolder={rootFolder}
            profileId={profileId}
            onCandidate={setCandidateKey}
            onRootFolder={setRootFolder}
            onProfile={setProfileId}
          />
        )}

        <DialogFooter>
          {(preview.data?.candidates.length ?? 0) > 0 && (
            <Button type="button" disabled={!canLink || linkMutation.isPending} onClick={handleLink}>
              {linkMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <Link2 className="size-4" />}
              {t("linkArr.confirm", { service: preview.data?.instance_name ?? "" })}
            </Button>
          )}
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            {t("common.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
