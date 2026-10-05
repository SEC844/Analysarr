import { describe, expect, it } from "vitest"

import { defaultAnalysarrUrl, realtimeSummary, sourceLabel, suggestsNightlyScan } from "@/lib/realtime"
import type { RealtimeStatus, SourceStatus } from "@/types/realtime"

const t = (key: string, vars?: Record<string, string | number>) => `${key} ${JSON.stringify(vars ?? {})}`

function status(overrides: Partial<RealtimeStatus> = {}): RealtimeStatus {
  return {
    active: true,
    sources: [],
    reconciliation: { enabled: true, mode: "interval", interval_minutes: 60, nightly_hour: 4 },
    ...overrides,
  }
}

function source(overrides: Partial<SourceStatus>): SourceStatus {
  return { key: "torrents", kind: "torrents", state: "active", last_event_at: null, last_check_at: null, error: null, ...overrides }
}

describe("realtimeSummary", () => {
  it("shows nothing while real time is off, red as soon as a source fails", () => {
    expect(realtimeSummary(undefined)).toBe("off")
    expect(realtimeSummary(status({ active: false }))).toBe("off")
    expect(realtimeSummary(status({ sources: [source({})] }))).toBe("ok")
    expect(realtimeSummary(status({ sources: [source({}), source({ key: "media_server", state: "error" })] }))).toBe(
      "error",
    )
  })
})

describe("suggestsNightlyScan", () => {
  it("offers the nightly scan only once real time is on and the schedule is something else", () => {
    expect(suggestsNightlyScan(status({ active: false }))).toBe(false)
    expect(suggestsNightlyScan(status())).toBe(true)
    expect(
      suggestsNightlyScan(status({ reconciliation: { enabled: true, mode: "nightly", interval_minutes: null, nightly_hour: 4 } })),
    ).toBe(false)
    expect(
      suggestsNightlyScan(status({ reconciliation: { enabled: false, mode: "nightly", interval_minutes: null, nightly_hour: 4 } })),
    ).toBe(true)
  })
})

describe("sourceLabel", () => {
  it("names each webhook after its instance", () => {
    const hooks = [{ service: "radarr" as const, instance_id: 2, name: "Radarr 4K", connected: true, url: null }]
    expect(sourceLabel(t, source({ key: "webhook:radarr:2", kind: "webhook" }), hooks)).toContain('"name":"Radarr 4K"')
    expect(sourceLabel(t, source({}), hooks)).toMatch(/^realtime\.sources\.torrents /)
  })
})

describe("defaultAnalysarrUrl", () => {
  it("keeps the saved address, otherwise this browser's", () => {
    expect(defaultAnalysarrUrl("http://analysarr:1818", "http://192.168.1.2:1818")).toBe("http://analysarr:1818")
    expect(defaultAnalysarrUrl("", "http://192.168.1.2:1818")).toBe("http://192.168.1.2:1818")
  })
})
