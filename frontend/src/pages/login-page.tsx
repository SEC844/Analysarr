import { useState } from "react"
import { Loader2, LogIn } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useLoginMutation } from "@/hooks/use-auth"
import { useI18n } from "@/i18n"

export function LoginPage() {
  const { t } = useI18n()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const loginMutation = useLoginMutation()

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    loginMutation.mutate({ username, password })
  }

  return (
    <div className="mx-auto flex min-h-svh max-w-sm flex-col justify-center px-4">
      <div className="mb-6 text-center">
        <h1 className="text-2xl font-semibold tracking-tight">Analysarr</h1>
        <p className="text-muted-foreground mt-1 text-sm">{t("auth.loginSubtitle")}</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{t("auth.loginTitle")}</CardTitle>
          <CardDescription>{t("auth.loginDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit}>
            <div className="space-y-1.5">
              <Label htmlFor="username">{t("common.username")}</Label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">{t("common.password")}</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </div>
            {loginMutation.isError && (
              <p className="text-destructive text-sm">{loginMutation.error.message}</p>
            )}
            <Button type="submit" className="w-full" disabled={loginMutation.isPending || !username || !password}>
              {loginMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <LogIn className="size-4" />}
              {t("auth.signIn")}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
