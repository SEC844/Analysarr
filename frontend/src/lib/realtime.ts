import type { I18nContextValue } from "@/i18n/core"
import type { RealtimeStatus, SourceStatus, WebhookRead } from "@/types/realtime"

type Translate = I18nContextValue["t"]

/** « Webhook Radarr 4K », « Client torrent », « Jellyfin »... */
export function sourceLabel(t: Translate, source: SourceStatus, webhooks: WebhookRead[]): string {
  if (source.kind === "torrents") return t("realtime.sources.torrents")
  if (source.kind === "media_server") return t("realtime.sources.media_server")
  const [, service, instance] = source.key.split(":")
  const hook = webhooks.find((w) => w.service === service && String(w.instance_id) === instance)
  return t("realtime.sources.webhook", { name: hook?.name ?? service ?? "" })
}

/** Ce que montre l'en-tête : rien tant que le temps réel est inactif ; une
 * pastille rouge dès qu'une source est en erreur. */
export function realtimeSummary(status: RealtimeStatus | undefined): "off" | "ok" | "error" {
  if (!status?.active) return "off"
  return status.sources.some((s) => s.state === "error") ? "error" : "ok"
}

/** Proposer le scan de nuit : temps réel actif et planification d'un autre type. */
export function suggestsNightlyScan(status: RealtimeStatus | undefined): boolean {
  if (!status?.active) return false
  const { enabled, mode } = status.reconciliation
  return !enabled || mode !== "nightly"
}

/** Adresse proposée pour les webhooks : celle déjà enregistrée, sinon celle de
 * ce navigateur (souvent la bonne quand tout tourne sur la même machine). */
export function defaultAnalysarrUrl(saved: string, origin: string): string {
  return saved || origin
}
