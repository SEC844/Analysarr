import { fireEvent, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ForecastSection } from "@/components/forecast/forecast-section"
import { en } from "@/i18n/en"
import { getForecast } from "@/lib/api"
import { renderWithProviders } from "@/test/render"
import type { DiskForecast, Forecast } from "@/types/forecast"

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getForecast: vi.fn(),
}))

const GB = 1024 ** 3

const DISK: DiskForecast = {
  key: "library+downloads",
  roles: ["library", "downloads"],
  paths: ["/data/media", "/data/torrents"],
  available: true,
  total: 1000 * GB,
  used: 600 * GB,
  free: 400 * GB,
  history: [
    { day: "2026-10-03", used: 590 * GB, total: 1000 * GB },
    { day: "2026-10-04", used: 595 * GB, total: 1000 * GB },
    { day: "2026-10-05", used: 600 * GB, total: 1000 * GB },
  ],
  history_days: 20,
  trend: { per_day: 5 * GB, low: 4 * GB, high: 7 * GB },
  fill: { earliest_days: 57, latest_days: 100, earliest_date: "2026-12-01", latest_date: "2027-01-13" },
  projection: [
    { day: "2026-10-05", used: 600 * GB, low: 600 * GB, high: 600 * GB },
    { day: "2026-10-12", used: 635 * GB, low: 628 * GB, high: 649 * GB },
  ],
}

const FORECAST: Forecast = {
  window_days: 30,
  min_history_days: 14,
  horizon_days: 90,
  history_days: 20,
  enough_history: true,
  latest_day: "2026-10-05",
  library: { size: 400 * GB, trend: { per_day: GB, low: GB / 2, high: 2 * GB } },
  disks: [DISK],
}

function renderSection(forecast: Forecast) {
  vi.mocked(getForecast).mockResolvedValue(forecast)
  renderWithProviders(<ForecastSection />)
}

describe("ForecastSection", () => {
  beforeEach(() => vi.clearAllMocks())

  it("shows the disk, its growth range and when it fills up", async () => {
    renderSection(FORECAST)

    expect(await screen.findByText("Library · Downloads")).toBeInTheDocument()
    expect(screen.getByText("Full in 8 to 14 weeks")).toBeInTheDocument()
    expect(screen.getByText("+152.2 GB per month")).toBeInTheDocument()
    expect(screen.getByText("between +121.8 GB and +213.1 GB")).toBeInTheDocument()
    expect(screen.getByText("60% of 1000.0 GB")).toBeInTheDocument()
    expect(screen.getByRole("img", { name: en.forecast.chart.label })).toBeInTheDocument()
    expect(screen.queryByText(/No forecast yet/)).not.toBeInTheDocument()
  })

  it("says honestly when there are not enough days yet", async () => {
    renderSection({
      ...FORECAST,
      enough_history: false,
      history_days: 6,
      disks: [{ ...DISK, trend: null, fill: null, projection: [], history_days: 6 }],
    })

    expect(await screen.findByText(/6 days of measurements out of the 14 needed/)).toBeInTheDocument()
    expect(screen.getAllByText(en.forecast.noTrend).length).toBeGreaterThan(0)
    // L'historique reste affiché.
    expect(screen.getByRole("img", { name: en.forecast.chart.label })).toBeInTheDocument()
  })

  it("offers the same data as a table", async () => {
    const user = userEvent.setup()
    renderSection(FORECAST)

    await user.click(await screen.findByRole("button", { name: en.forecast.table.show }))

    const table = screen.getByRole("table")
    expect(within(table).getByText("10/05/2026")).toBeInTheDocument()
    expect(within(table).getByText("10/12/2026 (expected)")).toBeInTheDocument()
    expect(within(table).getByText("628.0 GB – 649.0 GB")).toBeInTheDocument()
  })

  it("shows the value under the pointer", async () => {
    renderSection(FORECAST)
    const chart = await screen.findByRole("img", { name: en.forecast.chart.label })
    const overlay = chart.querySelector("rect")
    if (!overlay) throw new Error("zone de survol absente")

    fireEvent.pointerMove(overlay, { clientX: 10_000 })

    expect(screen.getByText("Expected: 635.0 GB")).toBeInTheDocument()
    expect(screen.getByText("between 628.0 GB and 649.0 GB")).toBeInTheDocument()
  })

  it("flags a folder that was not mounted", async () => {
    renderSection({ ...FORECAST, disks: [{ ...DISK, available: false, total: null, used: null, free: null }] })
    expect(await screen.findByText(en.forecast.unavailable)).toBeInTheDocument()
  })

  it("explains when nothing was measured yet", async () => {
    renderSection({ ...FORECAST, latest_day: null, library: null, disks: [], history_days: 0, enough_history: false })
    expect(await screen.findByText(en.forecast.empty)).toBeInTheDocument()
  })
})
