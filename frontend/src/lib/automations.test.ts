import { describe, expect, it } from "vitest"

import { GIGABYTE, MAX_MEDIA_DELETIONS, cleanupRuleInvalid, maxActionsFor } from "@/lib/automations"
import type { AutomationWrite } from "@/types/automations"

function rule(overrides: Partial<AutomationWrite> = {}): AutomationWrite {
  return {
    name: "r",
    enabled: true,
    trigger: "cleanup_candidate",
    action: "delete_media",
    conditions: { media_types: [], min_score: 70, min_reclaimable_bytes: 5 * GIGABYTE },
    max_actions: 5,
    dry_run: true,
    ...overrides,
  }
}

describe("cleanupRuleInvalid", () => {
  it("accepts a rule above both floors", () => {
    expect(cleanupRuleInvalid(rule())).toBe(false)
  })

  it.each([
    { min_score: null, min_reclaimable_bytes: 5 * GIGABYTE },
    { min_score: 49, min_reclaimable_bytes: 5 * GIGABYTE },
    { min_score: 101, min_reclaimable_bytes: 5 * GIGABYTE },
    { min_score: 70, min_reclaimable_bytes: null },
    { min_score: 70, min_reclaimable_bytes: GIGABYTE / 2 },
  ])("refuses %o", (conditions) => {
    expect(cleanupRuleInvalid(rule({ conditions: { media_types: [], ...conditions } }))).toBe(true)
  })

  it("does not concern other triggers", () => {
    expect(cleanupRuleInvalid(rule({ trigger: "orphan_detected", action: "cleanup", conditions: { media_types: [] } }))).toBe(
      false,
    )
  })
})

describe("maxActionsFor", () => {
  it("caps whole-media deletion lower than the other actions", () => {
    expect(maxActionsFor("delete_media")).toBe(MAX_MEDIA_DELETIONS)
    expect(maxActionsFor("cleanup")).toBe(50)
  })
})
