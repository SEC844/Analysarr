import { Bug, CheckCircle2, Code, ExternalLink, Loader2, RefreshCw, Star } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { PulseDot } from "@/components/ui/pulse-dot"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { useAppInfoQuery, useCheckUpdatesMutation, useSaveAppPreferencesMutation } from "@/hooks/use-app"
import { useI18n } from "@/i18n"
import { formatDate, formatDateTime, formatVersion } from "@/lib/format"
import type { AppInfo } from "@/types/app"

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right">{children}</span>
    </div>
  )
}

function AboutCard({ info }: { info: AppInfo }) {
  const { t, language } = useI18n()
  const checkUpdates = useCheckUpdatesMutation()
  const savePreferences = useSaveAppPreferencesMutation()

  const isRelease = /^\d+\.\d+\.\d+$/.test(info.version)
  const update = info.update
  const image = `ghcr.io/${info.repository_url.replace("https://github.com/", "").toLowerCase()}:latest`

  const handleError = (err: unknown) => toast.error(err instanceof Error ? err.message : t("common.saveFailed"))

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>{t("application.aboutTitle")}</CardTitle>
            <CardDescription>{t("application.aboutDescription")}</CardDescription>
          </div>
          {!isRelease ? (
            <Badge variant="outline">{t("application.devBuild")}</Badge>
          ) : update?.update_available ? (
            <Badge variant="outline" className="border-transparent bg-sky-500/10 text-sky-600 dark:text-sky-400">
              <PulseDot label={t("application.updateAvailable")} />
              {t("application.updateAvailable")}
            </Badge>
          ) : (
            update &&
            !update.error && (
              <Badge variant="outline" className="border-transparent bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                <CheckCircle2 className="size-3" />
                {t("application.upToDate")}
              </Badge>
            )
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="divide-border divide-y">
          <InfoRow label={t("application.version")}>
            <span className="font-medium">{formatVersion(info.version)}</span>
          </InfoRow>
          <InfoRow label={t("application.revision")}>
            {info.revision ? (
              <a
                href={`${info.repository_url}/commit/${info.revision}`}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-xs hover:underline"
              >
                {info.revision.slice(0, 7)}
              </a>
            ) : (
              "—"
            )}
          </InfoRow>
          <InfoRow label={t("application.buildDate")}>{formatDateTime(info.build_date) ?? "—"}</InfoRow>
        </div>

        {update?.update_available && update.latest_version && (
          <div className="space-y-2 rounded-md border border-sky-500/30 bg-sky-500/10 p-3 text-sm">
            <p className="font-medium">
              {t("application.newVersion", {
                version: formatVersion(update.latest_version),
                date: formatDate(update.published_at) ?? "—",
              })}
            </p>
            <p className="text-muted-foreground">{t("application.updateHowTo")}</p>
            <code className="bg-muted/50 block break-all rounded-md border px-2 py-1.5 font-mono text-xs">
              docker pull {image}
            </code>
            {update.release_url && (
              <Button
                variant="outline"
                size="sm"
                render={<a href={update.release_url} target="_blank" rel="noopener noreferrer" />}
              >
                <ExternalLink className="size-4" />
                {t("application.releaseNotes")}
              </Button>
            )}
          </div>
        )}
        {update?.error && (
          <p className="text-destructive text-sm">{t("application.checkFailed", { error: update.error })}</p>
        )}
        {update && !update.error && !update.update_available && (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            {isRelease ? (
              <>
                <CheckCircle2 className="size-4 text-emerald-500" />
                {t("application.upToDateMessage")}
              </>
            ) : (
              t("application.devBuildMessage", { version: formatVersion(update.latest_version ?? "—") })
            )}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-muted-foreground text-sm">
            {update
              ? t("application.lastChecked", { date: formatDateTime(update.checked_at) ?? "—" })
              : t("application.neverChecked")}
          </p>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={checkUpdates.isPending}
            onClick={() => checkUpdates.mutate(undefined, { onError: handleError })}
          >
            {checkUpdates.isPending ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            {t("application.checkNow")}
          </Button>
        </div>

        <div className="flex items-center justify-between gap-4 border-t pt-4">
          <div className="space-y-1">
            <Label htmlFor="update-check-enabled">{t("application.autoCheck")}</Label>
            <p className="text-muted-foreground text-sm">{t("application.autoCheckHelp")}</p>
          </div>
          <Switch
            id="update-check-enabled"
            checked={info.update_check_enabled}
            disabled={savePreferences.isPending}
            onCheckedChange={(checked) =>
              savePreferences.mutate({ language, update_check_enabled: checked }, { onError: handleError })
            }
          />
        </div>

        <div className="flex flex-wrap gap-2 border-t pt-4">
          <Button variant="ghost" size="sm" render={<a href={info.repository_url} target="_blank" rel="noopener noreferrer" />}>
            <Code className="size-4" />
            {t("application.sourceCode")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            render={<a href={`${info.repository_url}/issues`} target="_blank" rel="noopener noreferrer" />}
          >
            <Bug className="size-4" />
            {t("application.reportIssue")}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

export function ApplicationSection() {
  const { t } = useI18n()
  const { data: info, isLoading } = useAppInfoQuery()

  if (isLoading) return <Skeleton className="h-64 w-full" />
  if (!info) return <p className="text-destructive">{t("common.apiUnreachable")}</p>

  return (
    <div className="space-y-6">
      <AboutCard info={info} />
      <StarCard repositoryUrl={info.repository_url} />
    </div>
  )
}

function StarCard({ repositoryUrl }: { repositoryUrl: string }) {
  const { t } = useI18n()
  return (
    <Card>
      <CardContent className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <Star className="mt-0.5 size-5 shrink-0 fill-amber-400 text-amber-400" />
          <div className="space-y-0.5">
            <p className="text-sm font-medium">{t("application.starTitle")}</p>
            <p className="text-muted-foreground text-sm">{t("application.starDescription")}</p>
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="shrink-0"
          render={<a href={repositoryUrl} target="_blank" rel="noopener noreferrer" />}
        >
          <Star className="size-4" />
          {t("application.starButton")}
        </Button>
      </CardContent>
    </Card>
  )
}
