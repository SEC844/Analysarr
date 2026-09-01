import { CheckCircle2, XCircle } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

export function ConnectionTestAlert({
  result,
}: {
  result: { success: boolean; message: string } | undefined
}) {
  if (!result) return null

  return (
    <Alert variant={result.success ? "default" : "destructive"} className="mt-3">
      {result.success ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />}
      <AlertTitle>{result.success ? "Connexion réussie" : "Échec de la connexion"}</AlertTitle>
      <AlertDescription>{result.message}</AlertDescription>
    </Alert>
  )
}
