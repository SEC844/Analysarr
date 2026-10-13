import { describe, expect, it } from "vitest"

import { detectedAddress, failingSource, realtimeSummary, sourceSection } from "@/lib/realtime"
import type { RealtimeStatus, SourceStatus } from "@/types/realtime"

function source(overrides: Partial<SourceStatus>): SourceStatus {
  return {
    key: "torrents",
    kind: "torrents",
    state: "active",
    last_event_at: null,
    last_check_at: null,
    error: null,
    ...overrides,
  }
}

function status(sources: SourceStatus[]): RealtimeStatus {
  return { active: sources.length > 0, address_set: true, sources }
}

describe("realtimeSummary", () => {
  it("stays off until a service is followed, then flags a failing source", () => {
    expect(realtimeSummary(undefined)).toBe("off")
    expect(realtimeSummary(status([]))).toBe("off")
    expect(realtimeSummary(status([source({})]))).toBe("ok")
    expect(realtimeSummary(status([source({}), source({ key: "media_server", kind: "media_server", state: "error" })]))).toBe(
      "error",
    )
  })
})

describe("sourceSection", () => {
  it("leads to the settings of the service concerned", () => {
    expect(sourceSection(source({ kind: "torrents" }))).toBe("qbittorrent")
    expect(sourceSection(source({ kind: "media_server" }))).toBe("emby")
    expect(sourceSection(source({ kind: "requests" }))).toBe("seer")
    expect(sourceSection(source({ kind: "webhook", key: "webhook:radarr:2" }))).toBe("radarr")
    expect(sourceSection(source({ kind: "webhook", key: "webhook:sonarr:0" }))).toBe("sonarr")
  })
})

describe("failingSource", () => {
  it("returns the first source in error", () => {
    const failing = source({ key: "requests", kind: "requests", state: "error", error: "HTTP 401" })
    expect(failingSource(status([source({}), failing]))).toBe(failing)
    expect(failingSource(status([source({})]))).toBeUndefined()
  })
})

describe("detectedAddress", () => {
  it("proposes the browser's own address", () => {
    expect(detectedAddress({ origin: "http://10.0.20.110:1818" })).toBe("http://10.0.20.110:1818")
  })
})
