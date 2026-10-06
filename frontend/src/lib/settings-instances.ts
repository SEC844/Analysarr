import type { ArrKind, SettingsRead, SettingsWrite } from "@/types/settings"

export interface RemovedInstance {
  service: ArrKind
  /** 0 = instance principale. */
  instanceId: number
  name: string
}

/** Instances Sonarr/Radarr enregistrées qui disparaissent à cet
 * enregistrement : principale dont l'adresse est vidée, ou instance
 * supplémentaire retirée de la liste. */
export function removedInstances(saved: SettingsRead, form: SettingsWrite): RemovedInstance[] {
  const removed: RemovedInstance[] = []
  for (const service of ["sonarr", "radarr"] as const) {
    if (saved[service].url && !form[`${service}_url`].trim()) {
      removed.push({ service, instanceId: 0, name: service === "sonarr" ? "Sonarr" : "Radarr" })
    }
  }
  const kept = new Set(form.arr_instances.map((instance) => instance.id).filter((id) => id !== null))
  for (const instance of saved.arr_instances) {
    if (!kept.has(instance.id)) removed.push({ service: instance.kind, instanceId: instance.id, name: instance.name })
  }
  return removed
}
