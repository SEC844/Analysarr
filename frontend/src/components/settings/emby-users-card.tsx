import { Loader2 } from "lucide-react"

import { UserAvatar } from "@/components/media/watch-stats"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import { useEmbyUsersQuery } from "@/hooks/use-media"
import { useI18n } from "@/i18n"

interface EmbyUsersCardProps {
  excluded: string[]
  onExcludedChange: (ids: string[]) => void
}

// Choix des comptes Emby comptés dans les quotas de visionnage : un compte
// de test ou une TV partagée fausserait « vu par 3/10 ».
export function EmbyUsersCard({ excluded, onExcludedChange }: EmbyUsersCardProps) {
  const { t } = useI18n()
  const { data: users, isLoading, isError } = useEmbyUsersQuery()
  const excludedIds = new Set(excluded)

  const setCounted = (id: string, counted: boolean) =>
    onExcludedChange(counted ? excluded.filter((x) => x !== id) : [...excluded, id])

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("watch.settingsTitle")}</CardTitle>
        <CardDescription>{t("watch.settingsDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading && (
          <div className="text-muted-foreground flex items-center gap-2 py-2 text-sm">
            <Loader2 className="size-4 animate-spin" /> {t("common.loading")}
          </div>
        )}
        {isError && <p className="text-destructive text-sm">{t("watch.usersLoadFailed")}</p>}
        {users && users.length === 0 && <p className="text-muted-foreground text-sm">{t("watch.noEmbyUsers")}</p>}
        {users && users.length > 0 && (
          <ul className="divide-border divide-y">
            {users.map((user) => (
              <li key={user.id} className="flex items-center gap-3 py-2">
                <UserAvatar user={user} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{user.name}</p>
                  {user.is_disabled && <p className="text-muted-foreground text-xs">{t("watch.disabled")}</p>}
                </div>
                <Switch
                  checked={!user.is_disabled && !excludedIds.has(user.id)}
                  disabled={user.is_disabled}
                  onCheckedChange={(checked) => setCounted(user.id, checked)}
                  aria-label={t("watch.countUser", { name: user.name })}
                />
              </li>
            ))}
          </ul>
        )}
        <p className="text-muted-foreground text-sm">{t("watch.settingsHint")}</p>
      </CardContent>
    </Card>
  )
}
