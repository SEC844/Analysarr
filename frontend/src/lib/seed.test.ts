import { describe, expect, it } from "vitest"

import { obligationText } from "@/lib/seed"
import type { SeedObligation } from "@/types/seed"

// Traduction factice : la clé et ses variables, pour vérifier le choix du texte.
const t = (key: string, vars?: Record<string, string | number>) => `${key} ${JSON.stringify(vars ?? {})}`

function obligation(overrides: Partial<SeedObligation>): SeedObligation {
  return { reason: "min_seed", until: "2026-10-12T10:00:00", tracker: null, min_days: 14, min_ratio: null, ...overrides }
}

describe("obligationText", () => {
  it("names the tracker when the rule comes from it", () => {
    const text = obligationText(t, obligation({ tracker: "tracker.example.org" }))
    expect(text).toMatch(/^seed\.obligation\.min_seed /)
    expect(text).toContain('"tracker":"tracker.example.org"')
    expect(text).toContain("2026")
  })

  it("falls back to the general sentence without tracker", () => {
    expect(obligationText(t, obligation({}))).toMatch(/^seed\.obligation\.min_seed_general /)
  })

  it("explains a pending ratio", () => {
    const text = obligationText(t, obligation({ reason: "min_ratio", tracker: "t.example", min_ratio: 1.5 }))
    expect(text).toMatch(/^seed\.obligation\.min_ratio /)
    expect(text).toContain('"ratio":"1.50"')
  })

  it("explains an unknown date", () => {
    expect(obligationText(t, obligation({ reason: "unknown_date", until: null }))).toMatch(
      /^seed\.obligation\.unknown_date /,
    )
  })
})
