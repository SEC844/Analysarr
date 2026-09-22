import { useState } from "react"
import { Copy, Eye, EyeOff, KeyRound, Loader2, ScrollText, ShieldCheck, ShieldOff, UserPen } from "lucide-react"
import { toast } from "sonner"
import { renderSVG } from "uqr"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useChangePasswordMutation,
  useChangeUsernameMutation,
  useCurrentUserQuery,
  useLoginHistoryQuery,
  useSaveSecuritySettingsMutation,
  useSecuritySettingsQuery,
  useTwoFactorDisableMutation,
  useTwoFactorEnableMutation,
  useTwoFactorSetupMutation,
} from "@/hooks/use-auth"
import { useI18n, type MessageKey } from "@/i18n"
import { copyText } from "@/lib/clipboard"
import { formatDateTime } from "@/lib/format"

const MIN_PASSWORD_LENGTH = 8

type Translate = ReturnType<typeof useI18n>["t"]

function showError(t: Translate) {
  return (err: unknown) => toast.error(err instanceof Error ? err.message : t("common.saveFailed"))
}

async function copyToClipboard(text: string, t: Translate) {
  // HTTPS, ou HTTP sur le réseau local uniquement (voir lib/clipboard.ts).
  if (await copyText(text)) toast.success(t("twoFactor.copied"))
  else toast.error(t("twoFactor.copyFailed"))
}

function Field({
  id,
  label,
  help,
  ...input
}: { id: string; label: string; help?: string } & React.ComponentProps<typeof Input>) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} {...input} />
      {help && <p className="text-muted-foreground text-sm">{help}</p>}
    </div>
  )
}

function UsernameCard({ username }: { username: string }) {
  const { t } = useI18n()
  const [newUsername, setNewUsername] = useState("")
  const [password, setPassword] = useState("")
  const changeUsername = useChangeUsernameMutation()
  const trimmed = newUsername.trim()

  function handleSubmit() {
    changeUsername.mutate(
      { username: trimmed, password },
      {
        onSuccess: () => {
          toast.success(t("account.usernameUpdated"))
          setNewUsername("")
          setPassword("")
        },
        onError: showError(t),
      },
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("account.usernameTitle")}</CardTitle>
        <CardDescription>{t("account.usernameDescription", { name: username })}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field
          id="new-username"
          label={t("account.newUsername")}
          value={newUsername}
          onChange={(e) => setNewUsername(e.target.value)}
          maxLength={64}
          autoComplete="username"
        />
        <Field
          id="username-password"
          label={t("account.currentPassword")}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
        <Button
          type="button"
          disabled={!trimmed || trimmed === username || !password || changeUsername.isPending}
          onClick={handleSubmit}
        >
          {changeUsername.isPending ? <Loader2 className="size-4 animate-spin" /> : <UserPen className="size-4" />}
          {t("account.usernameSubmit")}
        </Button>
      </CardContent>
    </Card>
  )
}

function PasswordCard() {
  const { t } = useI18n()
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const changePassword = useChangePasswordMutation()

  const mismatch = confirm.length > 0 && newPassword !== confirm
  const canSubmit = !!currentPassword && newPassword.length >= MIN_PASSWORD_LENGTH && newPassword === confirm

  async function handleSubmit() {
    try {
      await changePassword.mutateAsync({ current_password: currentPassword, new_password: newPassword })
      toast.success(t("account.updated"))
      setCurrentPassword("")
      setNewPassword("")
      setConfirm("")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("account.failed"))
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("account.title")}</CardTitle>
        <CardDescription>{t("account.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field
          id="current-password"
          label={t("account.currentPassword")}
          type="password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          autoComplete="current-password"
        />
        <Field
          id="new-password"
          label={t("account.newPassword")}
          type="password"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          autoComplete="new-password"
        />
        <div className="space-y-1.5">
          <Label htmlFor="confirm-password">{t("common.confirmation")}</Label>
          <Input
            id="confirm-password"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
          />
          {mismatch && <p className="text-destructive text-sm">{t("common.passwordsMismatch")}</p>}
        </div>
        <Button type="button" disabled={!canSubmit || changePassword.isPending} onClick={handleSubmit}>
          {changePassword.isPending ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" />}
          {t("account.submit")}
        </Button>
        <p className="text-muted-foreground text-sm">{t("account.hint")}</p>
      </CardContent>
    </Card>
  )
}

type TwoFactorStep = { kind: "idle" } | { kind: "setup"; secret: string; uri: string } | { kind: "codes"; codes: string[] }

// Clé base32 lisible : groupes de 4 caractères.
const formatSecret = (secret: string) => secret.match(/.{1,4}/g)?.join(" ") ?? secret

function TwoFactorCard({ enabled }: { enabled: boolean }) {
  const { t } = useI18n()
  const [step, setStep] = useState<TwoFactorStep>({ kind: "idle" })
  const [password, setPassword] = useState("")
  const [code, setCode] = useState("")
  const [showKey, setShowKey] = useState(false)
  const setup = useTwoFactorSetupMutation()
  const enable = useTwoFactorEnableMutation()
  const disable = useTwoFactorDisableMutation()
  const onError = showError(t)

  function reset() {
    setStep({ kind: "idle" })
    setPassword("")
    setCode("")
    setShowKey(false)
  }

  function handleSetup() {
    setup.mutate(password, {
      onSuccess: (data) => {
        setPassword("")
        setStep({ kind: "setup", secret: data.secret, uri: data.otpauth_uri })
      },
      onError,
    })
  }

  function handleEnable() {
    enable.mutate(code, {
      onSuccess: (data) => {
        setCode("")
        setShowKey(false)
        setStep({ kind: "codes", codes: data.codes })
        toast.success(t("twoFactor.enabledToast"))
      },
      onError,
    })
  }

  function handleDisable() {
    disable.mutate(
      { password, code },
      {
        onSuccess: () => {
          reset()
          toast.success(t("twoFactor.disabledToast"))
        },
        onError,
      },
    )
  }

  let content: React.ReactNode
  if (step.kind === "codes") {
    content = (
      <>
        <div className="space-y-1">
          <p className="text-sm font-medium">{t("twoFactor.recoveryTitle")}</p>
          <p className="text-muted-foreground text-sm">{t("twoFactor.recoveryHelp")}</p>
        </div>
        <ul className="bg-muted/50 grid grid-cols-2 gap-2 rounded-md border p-3 font-mono text-sm select-all">
          {step.codes.map((recoveryCode) => (
            <li key={recoveryCode}>{recoveryCode}</li>
          ))}
        </ul>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" onClick={() => copyToClipboard(step.codes.join("\n"), t)}>
            <Copy className="size-4" />
            {t("twoFactor.copyCodes")}
          </Button>
          <Button type="button" onClick={reset}>
            {t("twoFactor.done")}
          </Button>
        </div>
      </>
    )
  } else if (step.kind === "setup") {
    // SVG généré localement (aucun service externe) et affiché comme image :
    // jamais injecté dans le DOM.
    const qrSrc = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(renderSVG(step.uri))}`
    content = (
      <>
        <p className="text-sm">{t("twoFactor.scan")}</p>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
          <img src={qrSrc} alt={t("twoFactor.qrAlt")} className="size-44 shrink-0 rounded-md bg-white p-2" />
          <div className="min-w-0 space-y-2">
            <p className="text-muted-foreground text-sm">{t("twoFactor.manual")}</p>
            {showKey ? (
              <code className="bg-muted/50 block rounded-md border px-2 py-1.5 font-mono text-sm break-all select-all">
                {formatSecret(step.secret)}
              </code>
            ) : (
              <p className="text-muted-foreground px-2 py-1.5 font-mono text-sm tracking-widest">•••• •••• •••• ••••</p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" size="sm" onClick={() => setShowKey((v) => !v)}>
                {showKey ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                {showKey ? t("twoFactor.hideKey") : t("twoFactor.showKey")}
              </Button>
              <Button type="button" variant="outline" size="sm" onClick={() => copyToClipboard(step.secret, t)}>
                <Copy className="size-4" />
                {t("twoFactor.copyKey")}
              </Button>
            </div>
          </div>
        </div>
        <Field
          id="totp-code"
          label={t("twoFactor.code")}
          help={t("twoFactor.codeHelp")}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          className="max-w-40 font-mono tracking-widest"
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" disabled={code.length !== 6 || enable.isPending} onClick={handleEnable}>
            {enable.isPending ? <Loader2 className="size-4 animate-spin" /> : <ShieldCheck className="size-4" />}
            {t("twoFactor.enable")}
          </Button>
          <Button type="button" variant="ghost" onClick={reset}>
            {t("common.cancel")}
          </Button>
        </div>
        <p className="text-muted-foreground text-sm">{t("twoFactor.revokeHint")}</p>
      </>
    )
  } else if (enabled) {
    content = (
      <>
        <p className="text-muted-foreground text-sm">{t("twoFactor.disableHelp")}</p>
        <Field
          id="disable-password"
          label={t("account.currentPassword")}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
        <Field
          id="disable-code"
          label={t("twoFactor.codeOrRecovery")}
          value={code}
          onChange={(e) => setCode(e.target.value.trim())}
          autoComplete="one-time-code"
          maxLength={32}
          className="max-w-56 font-mono tracking-widest"
        />
        <Button
          type="button"
          variant="destructive"
          disabled={!password || !code || disable.isPending}
          onClick={handleDisable}
        >
          {disable.isPending ? <Loader2 className="size-4 animate-spin" /> : <ShieldOff className="size-4" />}
          {t("twoFactor.disable")}
        </Button>
      </>
    )
  } else {
    content = (
      <>
        <Field
          id="setup-password"
          label={t("account.currentPassword")}
          help={t("twoFactor.startHelp")}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
        <Button type="button" disabled={!password || setup.isPending} onClick={handleSetup}>
          {setup.isPending ? <Loader2 className="size-4 animate-spin" /> : <ShieldCheck className="size-4" />}
          {t("twoFactor.start")}
        </Button>
      </>
    )
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>{t("twoFactor.title")}</CardTitle>
            <CardDescription>{t("twoFactor.description")}</CardDescription>
          </div>
          {enabled ? (
            <Badge variant="outline" className="border-transparent bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
              <ShieldCheck className="size-3" />
              {t("twoFactor.enabled")}
            </Badge>
          ) : (
            <Badge variant="outline">{t("twoFactor.disabled")}</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">{content}</CardContent>
    </Card>
  )
}

/** Proxys de confiance + dernières tentatives de connexion : de quoi vérifier
 * qu'une instance exposée n'est pas sondée, et que l'adresse affichée est bien
 * celle du client et non celle du reverse-proxy. */
function SecurityCard() {
  const { t } = useI18n()
  const { data: security } = useSecuritySettingsQuery()
  const { data: history } = useLoginHistoryQuery()
  const saveMutation = useSaveSecuritySettingsMutation()
  const [value, setValue] = useState<string | null>(null)

  const proxies = value ?? security?.trusted_proxies ?? ""

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ScrollText className="size-4" />
          {t("security.title")}
        </CardTitle>
        <CardDescription>{t("security.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-1.5">
          <Label htmlFor="trusted-proxies">{t("security.trustedProxies")}</Label>
          <div className="flex gap-2">
            <Input
              id="trusted-proxies"
              value={proxies}
              placeholder="10.0.0.0/24, 172.18.0.2"
              onChange={(e) => setValue(e.target.value)}
            />
            <Button
              type="button"
              variant="outline"
              disabled={saveMutation.isPending || value === null || value === security?.trusted_proxies}
              onClick={() =>
                saveMutation.mutate(proxies, {
                  onSuccess: () => {
                    setValue(null)
                    toast.success(t("security.saved"))
                  },
                  onError: showError(t),
                })
              }
            >
              {saveMutation.isPending && <Loader2 className="size-4 animate-spin" />}
              {t("common.save")}
            </Button>
          </div>
          <p className="text-muted-foreground text-xs">{t("security.trustedProxiesHelp")}</p>
        </div>

        <div className="space-y-2">
          <p className="text-sm font-medium">{t("security.history")}</p>
          {history && history.length > 0 ? (
            <ul className="divide-border divide-y text-sm">
              {history.map((attempt, index) => (
                <li key={`${attempt.created_at}-${index}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5">
                  <span className="text-muted-foreground tabular-nums">{formatDateTime(attempt.created_at)}</span>
                  <span className="font-mono text-xs">{attempt.ip || "—"}</span>
                  <span className="min-w-0 flex-1 truncate">{attempt.username || "—"}</span>
                  <Badge variant={attempt.success ? "secondary" : "destructive"}>
                    {attempt.success
                      ? t("security.success")
                      : t(`security.reasons.${attempt.reason ?? "password"}` as MessageKey)}
                  </Badge>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground text-sm">{t("security.historyEmpty")}</p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export function AccountSection() {
  const { t } = useI18n()
  const { data: user, isLoading } = useCurrentUserQuery(true)

  if (isLoading) return <Skeleton className="h-64 w-full" />
  if (!user) return <p className="text-destructive">{t("common.apiUnreachable")}</p>

  return (
    <div className="space-y-6">
      <UsernameCard username={user.username} />
      <PasswordCard />
      <TwoFactorCard enabled={user.two_factor_enabled} />
      <SecurityCard />
    </div>
  )
}
