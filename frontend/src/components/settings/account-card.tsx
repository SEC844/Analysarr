import { useState } from "react"
import { KeyRound, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useChangePasswordMutation } from "@/hooks/use-auth"

const MIN_PASSWORD_LENGTH = 8

export function AccountCard() {
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const changePassword = useChangePasswordMutation()

  const mismatch = confirm.length > 0 && newPassword !== confirm
  const canSubmit = !!currentPassword && newPassword.length >= MIN_PASSWORD_LENGTH && newPassword === confirm

  async function handleSubmit() {
    try {
      await changePassword.mutateAsync({ current_password: currentPassword, new_password: newPassword })
      toast.success("Mot de passe mis à jour.")
      setCurrentPassword("")
      setNewPassword("")
      setConfirm("")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Échec du changement de mot de passe.")
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Compte</CardTitle>
        <CardDescription>Changer le mot de passe du compte administrateur.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="current-password">Mot de passe actuel</Label>
          <Input
            id="current-password"
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            autoComplete="current-password"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="new-password">Nouveau mot de passe</Label>
          <Input
            id="new-password"
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            autoComplete="new-password"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="confirm-password">Confirmation</Label>
          <Input
            id="confirm-password"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
          />
          {mismatch && <p className="text-destructive text-sm">Les mots de passe ne correspondent pas.</p>}
        </div>
        <Button type="button" disabled={!canSubmit || changePassword.isPending} onClick={handleSubmit}>
          {changePassword.isPending ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" />}
          Mettre à jour le mot de passe
        </Button>
        <p className="text-muted-foreground text-sm">
          Changer le mot de passe déconnecte automatiquement toutes les autres sessions actives.
        </p>
      </CardContent>
    </Card>
  )
}
