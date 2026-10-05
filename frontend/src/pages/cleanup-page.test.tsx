import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { en } from "@/i18n/en"
import {
  deleteSelectionExecute,
  getCleanupCandidate,
  getCleanupCandidates,
  getCleanupSettings,
  getForecast,
  getMedia,
  saveCleanupSettings,
  simulateCleanupPlan,
} from "@/lib/api"
import { CleanupPage } from "@/pages/cleanup-page"
import { renderWithProviders } from "@/test/render"
import type { CleanupCandidate, CleanupCandidateDetail, CleanupSettings } from "@/types/cleanup"
import type { MediaDetail } from "@/types/media"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getCleanupCandidates: vi.fn(),
  getCleanupCandidate: vi.fn(),
  getCleanupSettings: vi.fn(),
  saveCleanupSettings: vi.fn(),
  getMedia: vi.fn(),
  deleteSelectionExecute: vi.fn(),
  getForecast: vi.fn(),
  simulateCleanupPlan: vi.fn(),
}))

function candidate(overrides: Partial<CleanupCandidate>): CleanupCandidate {
  return {
    media_id: 1,
    media_type: "movie",
    title: "Forgotten",
    year: 2010,
    has_poster: false,
    poster_image_tag: null,
    arr_instance_name: null,
    score: 82,
    rank: 80,
    reclaimable_bytes: 4 * 1024 ** 3,
    main_reason: "disinterest",
    malus_in_progress: false,
    protections: [],
    last_played_at: "2024-01-01T00:00:00",
    date_added: "2023-01-01T00:00:00",
    series_status: null,
    active_users: 1,
    ...overrides,
  }
}

const FORGOTTEN = candidate({})
const OLD_SERIES = candidate({
  media_id: 2,
  title: "Old series",
  media_type: "series",
  score: 55,
  reclaimable_bytes: 2 * 1024 ** 3,
})
const FAVOURITE = candidate({
  media_id: 3,
  title: "Loved",
  protections: [{ kind: "favorite", names: ["Marie"], days: null, obligation: null }],
})

const BALANCED: CleanupSettings = {
  preset: "balanced",
  disinterest_days: 365,
  age_days: 730,
  inactive_days: 90,
  recent_days: 30,
  space_priority: 30,
  weights: { disinterest: 35, potential: 35, age: 15, series: 15 },
}
const PRUDENT: CleanupSettings = {
  ...BALANCED,
  preset: "prudent",
  disinterest_days: 548,
  space_priority: 0,
  weights: { disinterest: 40, potential: 40, age: 10, series: 10 },
}

function detail(of: CleanupCandidate, overrides: Partial<CleanupCandidateDetail> = {}): CleanupCandidateDetail {
  return { ...of, components: [], raw_score: of.score, in_progress_users: [], ...overrides }
}

function media(id: number): MediaDetail {
  return {
    id,
    radarr_id: 10 + id,
    sonarr_id: null,
    files: [{ id: 100 + id }],
    torrents: [{ id: 200 + id }],
  } as unknown as MediaDetail
}

function renderPage() {
  renderWithProviders(
    <MemoryRouter>
      <CleanupPage />
    </MemoryRouter>,
  )
}

describe("CleanupPage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getForecast).mockResolvedValue({
      window_days: 30,
      min_history_days: 14,
      horizon_days: 90,
      history_days: 0,
      enough_history: false,
      latest_day: null,
      library: null,
      disks: [],
    })
    vi.mocked(getCleanupCandidates).mockImplementation(async (query) => ({
      items: query.include_protected ? [FORGOTTEN, OLD_SERIES, FAVOURITE] : [FORGOTTEN, OLD_SERIES],
      total: query.include_protected ? 3 : 2,
      candidate_count: 2,
      protected_count: 1,
      total_reclaimable_bytes: 6 * 1024 ** 3,
    }))
    vi.mocked(getCleanupCandidate).mockImplementation(async (id) =>
      detail(id === 1 ? FORGOTTEN : OLD_SERIES, {
        components: [
          {
            key: "disinterest",
            value: 100,
            weight: 50,
            contribution: 50,
            days: 600,
            since: "last_played",
            users: null,
            unfinished: null,
            series_status: null,
          },
        ],
      }),
    )
    vi.mocked(getCleanupSettings).mockResolvedValue({
      settings: BALANCED,
      presets: { prudent: PRUDENT, balanced: BALANCED, space_first: { ...BALANCED, preset: "space_first" } },
    })
    vi.mocked(saveCleanupSettings).mockImplementation(async (settings) => ({
      settings,
      presets: { prudent: PRUDENT, balanced: BALANCED, space_first: { ...BALANCED, preset: "space_first" } },
    }))
    vi.mocked(getMedia).mockImplementation(async (id) => media(id))
    vi.mocked(deleteSelectionExecute).mockResolvedValue({ steps: [], media_deleted: true })
  })

  it("lists the candidates with their score, space and main reason", async () => {
    renderPage()

    expect(await screen.findByText("Forgotten")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /82/ })).toHaveAttribute("title", en.cleanup.scoreHint)
    expect(screen.getAllByText("4.0 GB").length).toBeGreaterThan(0)
    expect(screen.getAllByText(/^Last played/).length).toBe(2)
    expect(screen.queryByText("Loved")).not.toBeInTheDocument()
  })

  it("shows protected media with their reason and no checkbox", async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText("Forgotten")

    await user.click(screen.getByRole("switch", { name: en.cleanup.showProtected }))

    expect(await screen.findByText("Loved")).toBeInTheDocument()
    expect(screen.getByText("Favourite of Marie")).toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "Select Loved" })).not.toBeInTheDocument()
    expect(getCleanupCandidates).toHaveBeenLastCalledWith(expect.objectContaining({ include_protected: true }))
  })

  it("explains the score on demand", async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole("button", { name: /82/ }))

    expect(await screen.findByText(en.cleanup.breakdown.title)).toBeInTheDocument()
    expect(screen.getByText("600 days without playback")).toBeInTheDocument()
    expect(getCleanupCandidate).toHaveBeenCalledWith(1)
  })

  it("adds up the selection", async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole("checkbox", { name: "Select Forgotten" }))
    expect(screen.getByText("1 media selected · 4.0 GB")).toBeInTheDocument()

    await user.click(screen.getByRole("checkbox", { name: "Select Old series" }))
    expect(screen.getByText("2 media selected · 6.0 GB")).toBeInTheDocument()
  })

  it("fills the selection from a goal, deletion still waiting for the dialog", async () => {
    const user = userEvent.setup()
    vi.mocked(simulateCleanupPlan).mockResolvedValue({
      goal: "free",
      target_bytes: 5 * 1024 ** 3,
      freed_bytes: 6 * 1024 ** 3,
      shortfall_bytes: 0,
      limited: false,
      max_items: 200,
      items: [FORGOTTEN, OLD_SERIES],
      losses: [],
      disk: null,
      free_bytes: null,
      days: null,
      growth_per_day: null,
    })
    renderPage()

    await user.click(await screen.findByRole("button", { name: en.cleanup.goal.simulate }))
    await user.click(await screen.findByRole("button", { name: en.cleanup.goal.select }))

    expect(screen.getByText("2 media selected · 6.0 GB")).toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "Select Forgotten" })).toBeChecked()
    expect(deleteSelectionExecute).not.toHaveBeenCalled()
  })

  it("never deletes anything when the dialog is closed", async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole("checkbox", { name: "Select Forgotten" }))
    await user.click(screen.getByRole("button", { name: en.cleanup.deleteSelection }))

    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getAllByRole("button", { name: en.common.close }).at(-1)!)

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    expect(deleteSelectionExecute).not.toHaveBeenCalled()
  })

  it("deletes each whole media through the existing deletion, once confirmed", async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole("checkbox", { name: "Select Forgotten" }))
    await user.click(screen.getByRole("checkbox", { name: "Select Old series" }))
    await user.click(screen.getByRole("button", { name: en.cleanup.deleteSelection }))
    const dialog = await screen.findByRole("dialog")
    await user.click(within(dialog).getByRole("checkbox", { name: en.cleanup.delete.removeFromArr }))

    await user.click(within(dialog).getByRole("button", { name: en.common.confirmDelete }))

    expect(await within(dialog).findByText("2 media deleted out of 2.")).toBeInTheDocument()
    expect(deleteSelectionExecute).toHaveBeenCalledWith(1, {
      torrent_ids: [201],
      media_file_ids: [101],
      remove_from_arr: true,
    })
    expect(deleteSelectionExecute).toHaveBeenCalledWith(2, {
      torrent_ids: [202],
      media_file_ids: [102],
      remove_from_arr: true,
    })
  })

  it("leaves aside a media that became protected before its turn", async () => {
    vi.mocked(getCleanupCandidate).mockResolvedValue(
      detail(FORGOTTEN, { protections: [{ kind: "favorite", names: ["Paul"], days: null, obligation: null }] }),
    )
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole("checkbox", { name: "Select Forgotten" }))
    await user.click(screen.getByRole("button", { name: en.cleanup.deleteSelection }))
    const dialog = await screen.findByRole("dialog")

    await user.click(within(dialog).getByRole("button", { name: en.common.confirmDelete }))

    expect(await within(dialog).findByText(en.cleanup.delete.skipped)).toBeInTheDocument()
    expect(deleteSelectionExecute).not.toHaveBeenCalled()
  })

  it("applies a preset from the ranking settings", async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole("button", { name: en.cleanup.settings.title }))

    await user.click(await screen.findByRole("button", { name: en.cleanup.settings.presets.prudent }))
    await user.click(screen.getByRole("button", { name: en.cleanup.settings.save }))

    await waitFor(() => expect(saveCleanupSettings).toHaveBeenCalledWith(PRUDENT))
  })

  it("refuses out of bound settings", async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole("button", { name: en.cleanup.settings.title }))
    const recent = await screen.findByLabelText(en.cleanup.settings.recentDays)

    await user.clear(recent)
    await user.type(recent, "999")

    expect(screen.getByRole("button", { name: en.cleanup.settings.save })).toBeDisabled()
    expect(screen.getByRole("alert")).toHaveTextContent(en.cleanup.settings.invalid)
  })
})
