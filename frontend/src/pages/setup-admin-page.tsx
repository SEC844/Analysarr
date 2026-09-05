import { useState } from "react"
import { Loader2, ShieldCheck } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useSetupAdminMutation } from "@/hooks/use-auth"

const MIN_PASSWORD_LENGTH = 8

export function SetupAdminPage() {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const setupMutation = useSetupAdminMutation()

  const passwordTooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH
  const passwordsMismatch = confirm.length > 0 && password !== confirm
  const canSubmit = !!username && password.length >= MIN_PASSWORD_LENGTH && password === confirm

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setupMutation.mutate({ username, password })
  }

  return (
    <div className="mx-auto flex min-h-svh max-w-sm flex-col justify-center px-4">
      <div className="mb-6 text-center">
        <h1 className="text-2xl font-semibold tracking-tight">Bienvenue sur Analysarr</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Créez le compte administrateur avant de continuer — c'est le seul compte de l'application.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Créer le compte administrateur</CardTitle>
          <CardDescription>Cet écran ne s'affiche qu'une seule fois.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit}>
            <div className="space-y-1.5">
              <Label htmlFor="username">Nom d'utilisateur</Label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Mot de passe</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
              />
              {passwordTooShort && (
                <p className="text-destructive text-sm">Au moins {MIN_PASSWORD_LENGTH} caractères.</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="confirm">Confirmation</Label>
              <Input
                id="confirm"
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
              />
              {passwordsMismatch && <p className="text-destructive text-sm">Les mots de passe ne correspondent pas.</p>}
            </div>
            {setupMutation.isError && (
              <p className="text-destructive text-sm">{setupMutation.error.message}</p>
            )}
            <Button type="submit" className="w-full" disabled={setupMutation.isPending || !canSubmit}>
              {setupMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <ShieldCheck className="size-4" />}
              Créer le compte
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
