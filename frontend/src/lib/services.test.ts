import { describe, expect, it } from "vitest"

import type { ServiceStatus } from "@/types/services"

import { summarizeServices } from "./services"

const service = (overrides: Partial<ServiceStatus>): ServiceStatus => ({
  service: "radarr",
  name: "Radarr",
  ok: true,
  message: "",
  ...overrides,
})

describe("summarizeServices", () => {
  it("groups instances by settings section and finds the first failing one", () => {
    const { bySection, firstDownSection } = summarizeServices({
      checked_at: "2026-09-15T10:00:00Z",
      services: [
        service({ service: "emby", name: "Emby" }),
        service({ service: "radarr", name: "Radarr" }),
        service({ service: "radarr", name: "Radarr 4K", ok: false, message: "Connexion impossible" }),
        service({ service: "cross_seed", name: "cross-seed", ok: false }),
      ],
    })

    expect(bySection.emby).toEqual({ ok: true, down: [] })
    expect(bySection.radarr.ok).toBe(false)
    expect(bySection.radarr.down.map((s) => s.name)).toEqual(["Radarr 4K"])
    expect(bySection["cross-seed"].ok).toBe(false)
    expect(firstDownSection).toBe("radarr")
  })

  it("reports nothing before the first check", () => {
    expect(summarizeServices(undefined)).toEqual({ bySection: {}, firstDownSection: null })
  })
})
