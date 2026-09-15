import { CheckCircle2, XCircle } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { useI18n } from "@/i18n"

export function ConnectionTestAlert({
  result,
}: {
  result: { success: boolean; message: string } | undefined
}) {
  const { t } = useI18n()
  if (!result) return null

  return (
    <Alert variant={result.success ? "default" : "destructive"} className="mt-3">
      {result.success ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />}
      <AlertTitle>{result.success ? t("common.connectionSuccess") : t("common.connectionFailed")}</AlertTitle>
      <AlertDescription>{result.message}</AlertDescription>
    </Alert>
  )
}
