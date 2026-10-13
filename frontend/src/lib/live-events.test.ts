import { describe, expect, it } from "vitest"

import { parseLiveEvent, planInvalidation } from "@/lib/live-events"

describe("planInvalidation", () => {
  it("merges a burst into one plan, a full update winning over watch activity", () => {
    const plan = planInvalidation([
      { type: "media.updated", media_id: 1, watch: true },
      { type: "media.updated", media_id: 1 },
      { type: "media.updated", media_id: 2, watch: true },
      { type: "media.updated", media_id: 2, watch: true },
    ])

    expect(plan.lists).toBe(true)
    expect(plan.details).toEqual([1])
    expect(plan.watches).toEqual([2])
    expect(plan.cleanup).toBe(false)
  })

  it("never re-reads a media that was removed in the same burst", () => {
    const plan = planInvalidation([
      { type: "media.updated", media_id: 3 },
      { type: "media.removed", media_id: 3 },
    ])

    expect(plan).toMatchObject({ details: [], watches: [], removed: [3], cleanup: true })
  })

  it("re-reads the library after a service analysis", () => {
    expect(planInvalidation([{ type: "library.changed", scope: "torrents" }])).toMatchObject({
      lists: true,
      cleanup: true,
      scans: true,
    })
  })

  it("re-reads everything after missed events", () => {
    const plan = planInvalidation([{ type: "media.updated", media_id: 1 }, { type: "resync" }])
    expect(plan).toEqual({
      lists: true,
      details: [],
      watches: [],
      removed: [],
      cleanup: true,
      realtime: true,
      scans: true,
    })
  })
})

describe("parseLiveEvent", () => {
  it("ignores anything that is not an event", () => {
    expect(parseLiveEvent('{"type":"cleanup.changed"}')).toEqual({ type: "cleanup.changed" })
    expect(parseLiveEvent("pas du json")).toBeNull()
    expect(parseLiveEvent("[1]")).toBeNull()
    expect(parseLiveEvent('{"media_id":1}')).toBeNull()
  })
})
