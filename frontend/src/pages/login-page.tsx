import { useState } from "react"
import { Loader2, LogIn } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useLoginMutation } from "@/hooks/use-auth"
import { useI18n } from "@/i18n"
import { isTwoFactorRequired } from "@/lib/api"

export function LoginPage() {
  const { t } = useI18n()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [otp, setOtp] = useState("")
  // Double authentification : le code n'est demandé qu'une fois le mot de
  // passe validé par le serveur.
  const [needsOtp, setNeedsOtp] = useState(false)
  const loginMutation = useLoginMutation()

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    loginMutation.mutate(needsOtp ? { username, password, otp } : { username, password }, {
      onError: (err) => {
        if (isTwoFactorRequired(err)) setNeedsOtp(true)
      },
    })
  }

  function backToPassword() {
    setNeedsOtp(false)
    setOtp("")
    loginMutation.reset()
  }

  // La première demande de code n'est pas une erreur à afficher.
  const firstOtpPrompt = isTwoFactorRequired(loginMutation.error) && !loginMutation.variables?.otp
  const error = loginMutation.isError && !firstOtpPrompt ? loginMutation.error.message : null

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
            {needsOtp ? (
              <div className="space-y-1.5">
                <Label htmlFor="otp">{t("auth.otpLabel")}</Label>
                <Input
                  id="otp"
                  value={otp}
                  onChange={(e) => setOtp(e.target.value.trim())}
                  autoComplete="one-time-code"
                  maxLength={32}
                  className="font-mono tracking-widest"
                  autoFocus
                />
                <p className="text-muted-foreground text-sm">{t("auth.otpHelp")}</p>
              </div>
            ) : (
              <>
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
              </>
            )}
            {error && <p className="text-destructive text-sm">{error}</p>}
            <Button
              type="submit"
              className="w-full"
              disabled={loginMutation.isPending || (needsOtp ? !otp : !username || !password)}
            >
              {loginMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <LogIn className="size-4" />}
              {t("auth.signIn")}
            </Button>
            {needsOtp && (
              <Button type="button" variant="ghost" className="w-full" onClick={backToPassword}>
                {t("common.previous")}
              </Button>
            )}
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
