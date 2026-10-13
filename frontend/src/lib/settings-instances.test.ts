import { describe, expect, it } from "vitest"

import { removedInstances } from "@/lib/settings-instances"
import { emptySettingsWrite, type SettingsRead, type SettingsWrite } from "@/types/settings"

function saved(overrides: Partial<SettingsRead>): SettingsRead {
  return {
    sonarr: { url: "http://sonarr", api_key_set: true },
    radarr: { url: null, api_key_set: false },
    arr_instances: [
      { id: 2, kind: "sonarr", name: "Sonarr 4K", url: "http://sonarr4k", api_key_set: true },
      { id: 3, kind: "radarr", name: "Radarr anime", url: "http://radarr2", api_key_set: true },
    ],
    ...overrides,
  } as SettingsRead
}

function form(overrides: Partial<SettingsWrite>): SettingsWrite {
  return { ...emptySettingsWrite(), sonarr_url: "http://sonarr", ...overrides }
}

describe("removedInstances", () => {
  it("lists the instances that disappear with this save", () => {
    const kept = [{ key: "a", id: 2, kind: "sonarr" as const, name: "Sonarr 4K", url: "http://sonarr4k", api_key: "" }]
    expect(removedInstances(saved({}), form({ arr_instances: kept }))).toEqual([
      { service: "radarr", instanceId: 3, name: "Radarr anime" },
    ])
  })

  it("counts a primary instance whose address is cleared", () => {
    expect(removedInstances(saved({ arr_instances: [] }), form({ sonarr_url: " " }))).toEqual([
      { service: "sonarr", instanceId: 0, name: "Sonarr" },
    ])
  })

  it("is empty when nothing is removed", () => {
    expect(removedInstances(saved({ arr_instances: [] }), form({}))).toEqual([])
  })
})
