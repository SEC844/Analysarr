import { describe, expect, it } from "vitest"

import { componentDetail, mainReasonText, protectionText, scoreTone, selectionSize } from "@/lib/cleanup"
import type { CleanupCandidate, ScoreComponent } from "@/types/cleanup"

// Traduction factice : la clé et ses variables, pour vérifier le choix du texte.
const t = (key: string, vars?: Record<string, string | number>) => `${key} ${JSON.stringify(vars ?? {})}`

function candidate(overrides: Partial<CleanupCandidate>): CleanupCandidate {
  return {
    media_id: 1,
    media_type: "movie",
    title: "Film",
    year: 2020,
    has_poster: false,
    poster_image_tag: null,
    arr_instance_name: null,
    score: 50,
    rank: 50,
    reclaimable_bytes: 1000,
    main_reason: "disinterest",
    malus_in_progress: false,
    protections: [],
    last_played_at: "2025-01-01T00:00:00",
    date_added: "2024-01-01T00:00:00",
    series_status: null,
    active_users: 2,
    ...overrides,
  }
}

function component(overrides: Partial<ScoreComponent>): ScoreComponent {
  return {
    key: "disinterest",
    value: 50,
    weight: 35,
    contribution: 17.5,
    days: 180,
    since: "last_played",
    users: null,
    unfinished: null,
    series_status: null,
    ...overrides,
  }
}

describe("scoreTone", () => {
  it("colours by relevance", () => {
    expect([scoreTone(90), scoreTone(70), scoreTone(69), scoreTone(40), scoreTone(10)]).toEqual([
      "high",
      "high",
      "medium",
      "medium",
      "low",
    ])
  })
})

describe("mainReasonText", () => {
  it("names the last playback, or the date added for a media never watched", () => {
    expect(mainReasonText(t, candidate({}))).toMatch(/^cleanup\.reasons\.disinterest_played /)
    expect(mainReasonText(t, candidate({ last_played_at: null }))).toMatch(/^cleanup\.reasons\.disinterest_added /)
    expect(mainReasonText(t, candidate({ last_played_at: null, date_added: null }))).toMatch(
      /^cleanup\.reasons\.disinterest_unknown /,
    )
  })

  it("tells apart nobody left from everybody done", () => {
    expect(mainReasonText(t, candidate({ main_reason: "potential", active_users: 0 }))).toMatch(
      /^cleanup\.reasons\.potential_nobody /,
    )
    expect(mainReasonText(t, candidate({ main_reason: "potential" }))).toMatch(/^cleanup\.reasons\.potential /)
  })

  it("gives the series status", () => {
    const text = mainReasonText(t, candidate({ main_reason: "series", series_status: "ended" }))
    expect(text).toMatch(/^cleanup\.reasons\.series /)
    expect(text).toContain("media.seriesStatus.ended")
  })

  it("says nothing without a reason", () => {
    expect(mainReasonText(t, candidate({ main_reason: null }))).toBeNull()
  })
})

describe("protectionText", () => {
  it("lists the accounts behind a favourite or a request", () => {
    expect(protectionText(t, { kind: "favorite", names: ["Marie", "Paul"], days: null, obligation: null })).toContain(
      "Marie, Paul",
    )
  })

  it("reuses the seed obligation sentence", () => {
    const text = protectionText(t, {
      kind: "seed",
      names: [],
      days: null,
      obligation: { reason: "unknown_date", until: null, tracker: null, min_days: 14, min_ratio: null },
    })
    expect(text).toMatch(/^seed\.obligation\.unknown_date /)
  })
})

describe("componentDetail", () => {
  it("explains each component from its raw values", () => {
    expect(componentDetail(t, component({}))).toMatch(/^cleanup\.breakdown\.disinterestPlayed .*"count":180/)
    expect(componentDetail(t, component({ since: "added" }))).toMatch(/^cleanup\.breakdown\.disinterestAdded /)
    expect(componentDetail(t, component({ days: null, since: null }))).toMatch(/^cleanup\.breakdown\.unknownDate /)
    expect(componentDetail(t, component({ key: "potential", users: 4, unfinished: 1 }))).toMatch(
      /^cleanup\.breakdown\.potential .*"unfinished":1,"users":4/,
    )
    expect(componentDetail(t, component({ key: "potential", users: 0 }))).toMatch(/^cleanup\.breakdown\.potentialNobody /)
    expect(componentDetail(t, component({ key: "age", days: 700 }))).toMatch(/^cleanup\.breakdown\.age .*"count":700/)
  })
})

describe("selectionSize", () => {
  it("adds up the space of the selection", () => {
    expect(selectionSize([{ reclaimable_bytes: 1024 }, { reclaimable_bytes: 1024 }])).toMatch(/^2\.0 /)
  })
})
