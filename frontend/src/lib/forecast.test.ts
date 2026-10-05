import { describe, expect, it } from "vitest"

import {
  byteTicks,
  dayFromToday,
  dayFromIndex,
  dayIndex,
  dayTicks,
  durationUnit,
  fillText,
  monthlyGrowth,
  nearestIndex,
  parseDay,
  signedBytes,
} from "@/lib/forecast"
import type { DiskForecast, FillEstimate, Trend } from "@/types/forecast"

// Traduction factice : la clé et ses variables, pour vérifier le choix du texte.
const t = (key: string, vars?: Record<string, string | number>) => `${key} ${JSON.stringify(vars ?? {})}`

const GB = 1024 ** 3
const TREND: Trend = { per_day: GB, low: GB / 2, high: 2 * GB }

function disk(fill: Partial<FillEstimate> | null, trend: Trend | null = TREND): DiskForecast {
  return {
    key: "library",
    roles: ["library"],
    paths: ["/media"],
    available: true,
    total: 1000 * GB,
    used: 500 * GB,
    free: 500 * GB,
    history: [],
    history_days: 20,
    trend,
    fill: fill && {
      earliest_days: 42,
      latest_days: 63,
      earliest_date: "2026-11-16",
      latest_date: "2026-12-07",
      ...fill,
    },
    projection: [],
  }
}

describe("calendar days", () => {
  it("reads a day as a local date, never shifted by the time zone", () => {
    const day = parseDay("2026-10-05")
    expect([day.getFullYear(), day.getMonth(), day.getDate(), day.getHours()]).toEqual([2026, 9, 5, 0])
    expect(dayIndex("2026-10-06") - dayIndex("2026-10-05")).toBe(1)
    // Passage à l'heure d'hiver : toujours un jour d'écart.
    expect(dayIndex("2026-10-26") - dayIndex("2026-10-25")).toBe(1)
    expect(dayFromIndex(dayIndex("2026-03-29"))).toBe("2026-03-29")
  })

  it("builds a day relative to today for the date field", () => {
    expect(dayFromToday(30, new Date(2026, 0, 15))).toBe("2026-02-14")
    expect(dayFromToday(0, new Date(2026, 11, 31))).toBe("2026-12-31")
  })
})

describe("durationUnit", () => {
  it.each([
    [3.4, "days", 3],
    [20, "weeks", 3],
    [182, "weeks", 26],
    [200, "months", 7],
    [900, "years", 2],
  ])("%s days read as %s", (days, unit, value) => {
    expect(durationUnit(days)).toEqual({ unit, value })
  })
})

describe("fillText", () => {
  it("gives a range in a shared unit", () => {
    expect(fillText(t, disk({}))).toBe('forecast.fill.range {"from":"6","to":"forecast.units.weeks {\\"count\\":9}"}')
  })

  it("spells both ends when their units differ", () => {
    expect(fillText(t, disk({ earliest_days: 10, latest_days: 300 }))).toBe(
      'forecast.fill.range {"from":"forecast.units.days {\\"count\\":10}","to":"forecast.units.months {\\"count\\":10}"}',
    )
  })

  it("only gives the earliest date when the low slope never fills the disk", () => {
    expect(fillText(t, disk({ latest_days: null }))).toBe(
      'forecast.fill.atEarliest {"duration":"forecast.units.weeks {\\"count\\":6}"}',
    )
  })

  it("says so when both ends round to the same value", () => {
    expect(fillText(t, disk({ earliest_days: 42, latest_days: 44 }))).toBe(
      'forecast.fill.about {"duration":"forecast.units.weeks {\\"count\\":6}"}',
    )
  })

  it("never invents a date", () => {
    expect(fillText(t, disk(null))).toBe("forecast.fill.never {}")
    expect(fillText(t, disk(null, null))).toBeNull()
    expect(fillText(t, disk({ earliest_days: 0.2 }))).toBe("forecast.fill.now {}")
  })
})

describe("growth", () => {
  it("signs sizes", () => {
    expect(signedBytes(2 * GB)).toBe("+2.0 Go")
    expect(signedBytes(-GB / 2)).toBe("−512.0 Mo")
    expect(signedBytes(0)).toBe("0 o")
  })

  it("turns a daily trend into a monthly one", () => {
    expect(monthlyGrowth({ per_day: GB, low: -GB / 10, high: 2 * GB })).toEqual({
      value: "+30.4 Go",
      low: "−3.0 Go",
      high: "+60.9 Go",
    })
  })
})

describe("chart scales", () => {
  it("places round ticks in the displayed unit", () => {
    expect(byteTicks(1000 * GB).map((v) => v / GB)).toEqual([0, 250, 500, 750, 1000])
    expect(byteTicks(4 * 1024 * GB).map((v) => v / (1024 * GB))).toEqual([0, 1, 2, 3, 4])
    expect(byteTicks(0)).toEqual([0])
  })

  it("spreads date labels over time and finds the nearest point", () => {
    expect(dayTicks(100, 190, 4)).toEqual([100, 130, 160, 190])
    expect(dayTicks(100, 102, 5)).toEqual([100, 101, 102])
    expect(dayTicks(100, 100, 5)).toEqual([100])
    expect(nearestIndex([0, 10, 20, 30], 17)).toBe(2)
  })
})
