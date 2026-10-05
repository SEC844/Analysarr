import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { CleanupGoalCard } from "@/components/cleanup/cleanup-goal-card"
import { en } from "@/i18n/en"
import { deleteSelectionExecute, getForecast, simulateCleanupPlan } from "@/lib/api"
import { dayFromToday } from "@/lib/forecast"
import { renderWithProviders } from "@/test/render"
import type { CleanupCandidate, CleanupPlan } from "@/types/cleanup"
import type { Forecast } from "@/types/forecast"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getForecast: vi.fn(),
  simulateCleanupPlan: vi.fn(),
  deleteSelectionExecute: vi.fn(),
}))

const GB = 1024 ** 3

const ITEM: CleanupCandidate = {
  media_id: 7,
  media_type: "movie",
  title: "Forgotten",
  year: 2010,
  has_poster: false,
  poster_image_tag: null,
  arr_instance_name: null,
  score: 80,
  rank: 80,
  reclaimable_bytes: 60 * GB,
  main_reason: "disinterest",
  malus_in_progress: false,
  protections: [],
  last_played_at: null,
  date_added: null,
  series_status: null,
  active_users: 0,
}

const PLAN: CleanupPlan = {
  goal: "free",
  target_bytes: 50 * GB,
  freed_bytes: 60 * GB,
  shortfall_bytes: 0,
  limited: false,
  max_items: 200,
  items: [ITEM],
  losses: [
    {
      user_id: "u1",
      name: "Lea",
      image_tag: null,
      media: [
        { media_id: 7, title: "Forgotten", media_type: "movie", in_progress: true, favorite: true, progress: 45 },
      ],
    },
  ],
  disk: null,
  free_bytes: null,
  days: null,
  growth_per_day: null,
}

function forecast(withTrend: boolean): Forecast {
  return {
    window_days: 30,
    min_history_days: 14,
    horizon_days: 90,
    history_days: withTrend ? 20 : 5,
    enough_history: withTrend,
    latest_day: "2026-10-05",
    library: null,
    disks: [
      {
        key: "library+downloads",
        roles: ["library", "downloads"],
        paths: ["/data"],
        available: true,
        total: 1000 * GB,
        used: 600 * GB,
        free: 400 * GB,
        history: [],
        history_days: withTrend ? 20 : 5,
        trend: withTrend ? { per_day: 5 * GB, low: 4 * GB, high: 7 * GB } : null,
        fill: null,
        projection: [],
      },
    ],
  }
}

function renderCard(withTrend = true) {
  vi.mocked(getForecast).mockResolvedValue(forecast(withTrend))
  const onSelect = vi.fn()
  renderWithProviders(<CleanupGoalCard onSelect={onSelect} />)
  return onSelect
}

describe("CleanupGoalCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(simulateCleanupPlan).mockResolvedValue(PLAN)
  })

  it("simulates a space goal and says who loses what, without deleting anything", async () => {
    const user = userEvent.setup()
    const onSelect = renderCard()
    const amount = screen.getByLabelText(en.cleanup.goal.amount)
    await user.clear(amount)
    await user.type(amount, "50")

    await user.click(screen.getByRole("button", { name: en.cleanup.goal.simulate }))

    expect(simulateCleanupPlan).toHaveBeenCalledWith({ goal: "free", target_bytes: 50 * GB })
    expect(await screen.findByText("1 media, 60.0 GB freed")).toBeInTheDocument()
    expect(screen.getByText("Lea")).toBeInTheDocument()
    expect(screen.getByText("· started (45%), favorite")).toBeInTheDocument()
    expect(deleteSelectionExecute).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: en.cleanup.goal.select }))
    expect(onSelect).toHaveBeenCalledWith([ITEM])
    expect(deleteSelectionExecute).not.toHaveBeenCalled()
  })

  it("refuses an amount the server would refuse", async () => {
    const user = userEvent.setup()
    renderCard()
    const amount = screen.getByLabelText(en.cleanup.goal.amount)
    await user.clear(amount)
    await user.type(amount, "0")

    expect(screen.getByText(en.cleanup.goal.invalidAmount)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: en.cleanup.goal.simulate })).toBeDisabled()
  })

  it("plans to last until a date on the forecast disk", async () => {
    const user = userEvent.setup()
    vi.mocked(simulateCleanupPlan).mockResolvedValue({
      ...PLAN,
      goal: "until",
      disk: "library+downloads",
      free_bytes: 400 * GB,
      days: 100,
      growth_per_day: 7 * GB,
      target_bytes: 315 * GB,
    })
    renderCard()

    await user.click(screen.getByRole("button", { name: en.cleanup.goal.until }))
    const date = await screen.findByLabelText(en.cleanup.goal.date)
    await user.clear(date)
    await user.type(date, dayFromToday(100))
    await user.click(screen.getByRole("button", { name: en.cleanup.goal.simulate }))

    await waitFor(() =>
      expect(simulateCleanupPlan).toHaveBeenCalledWith({
        goal: "until",
        until: dayFromToday(100),
        disk: "library+downloads",
      }),
    )
    expect(await screen.findByText(/400.0 GB free today, prudent growth of 7.0 GB per day/)).toBeInTheDocument()
  })

  it("says when the disk already lasts until the date", async () => {
    const user = userEvent.setup()
    vi.mocked(simulateCleanupPlan).mockResolvedValue({ ...PLAN, goal: "until", target_bytes: 0, items: [], losses: [] })
    renderCard()

    await user.click(screen.getByRole("button", { name: en.cleanup.goal.until }))
    await user.click(await screen.findByRole("button", { name: en.cleanup.goal.simulate }))

    expect(await screen.findByText(/without deleting anything/)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: en.cleanup.goal.select })).not.toBeInTheDocument()
  })

  it("waits for enough history before planning by date", async () => {
    const user = userEvent.setup()
    renderCard(false)

    await user.click(screen.getByRole("button", { name: en.cleanup.goal.until }))

    expect(await screen.findByText("Available after 14 days of measurements of the disk.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: en.cleanup.goal.simulate })).toBeDisabled()
  })
})
