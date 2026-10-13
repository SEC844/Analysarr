import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, useLocation } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { en } from "@/i18n/en"
import {
  deleteSelectionExecute,
  getCleanupCandidate,
  getCleanupOtherLinks,
  getCleanupCandidates,
  getCleanupSettings,
  getForecast,
  getMedia,
  saveCleanupSettings,
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
  getCleanupOtherLinks: vi.fn(),
  getCleanupSettings: vi.fn(),
  saveCleanupSettings: vi.fn(),
  getMedia: vi.fn(),
  deleteSelectionExecute: vi.fn(),
  getForecast: vi.fn(),
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
  const space = { on_disk_bytes: of.reclaimable_bytes, freed_bytes: of.reclaimable_bytes, held_bytes: 0, external_links: 0 }
  return { ...of, components: [], raw_score: of.score, in_progress_users: [], space, ...overrides }
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

function LocationProbe() {
  const { search } = useLocation()
  return <output data-testid="location">{search}</output>
}

function renderPage(url = "/cleanup") {
  return renderWithProviders(
    <MemoryRouter initialEntries={[url]}>
      <CleanupPage />
      <LocationProbe />
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
      unreliable_sources: [],
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

  it("explains space held by links outside the media, and finds them on demand", async () => {
    const user = userEvent.setup()
    vi.mocked(getCleanupCandidate).mockResolvedValue(
      detail(FORGOTTEN, {
        space: { on_disk_bytes: 6 * 1024 ** 3, freed_bytes: 0, held_bytes: 6 * 1024 ** 3, external_links: 1 },
      }),
    )
    vi.mocked(getCleanupOtherLinks).mockResolvedValue({
      paths: ["/data/torrents/cross-seed-links/Forgotten.mkv"],
      complete: true,
      roots: ["/data"],
    })
    renderPage()

    await user.click(await screen.findByRole("button", { name: /82/ }))

    expect(await screen.findByText("0 B of 6.0 GB")).toBeInTheDocument()
    expect(screen.getByText(/6.0 GB would stay used: another link/)).toBeInTheDocument()
    expect(getCleanupOtherLinks).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: en.cleanup.space.find }))

    expect(await screen.findByText("/data/torrents/cross-seed-links/Forgotten.mkv")).toBeInTheDocument()
    expect(getCleanupOtherLinks).toHaveBeenCalledWith(1)
  })

  it("adds up the selection", async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole("checkbox", { name: "Select Forgotten" }))
    expect(screen.getByText("1 media selected · 4.0 GB")).toBeInTheDocument()

    await user.click(screen.getByRole("checkbox", { name: "Select Old series" }))
    expect(screen.getByText("2 media selected · 6.0 GB")).toBeInTheDocument()
  })

  it("warns when the last full scan could not read watch data", async () => {
    vi.mocked(getCleanupCandidates).mockResolvedValue({
      items: [FORGOTTEN],
      total: 1,
      candidate_count: 1,
      protected_count: 0,
      total_reclaimable_bytes: 4 * 1024 ** 3,
      unreliable_sources: ["watch"],
    })
    renderPage()

    expect(await screen.findByText(en.cleanup.unreliable.title)).toBeInTheDocument()
    expect(screen.getByText(en.cleanup.unreliable.watch)).toBeInTheDocument()
    expect(screen.queryByText(en.cleanup.unreliable.requests)).not.toBeInTheDocument()
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

  it("shares the hundred points of the score between the criteria", async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole("button", { name: en.cleanup.settings.title }))
    const slider = (key: "disinterest" | "potential" | "age" | "series") =>
      screen.getByLabelText(en.cleanup.breakdown.components[key])
    const save = () => screen.getByRole("button", { name: en.cleanup.settings.save })

    // Tous les points sont déjà répartis : le curseur ne va pas plus loin.
    fireEvent.change(await screen.findByLabelText(en.cleanup.breakdown.components.age), { target: { value: "40" } })
    expect(slider("age")).toHaveValue("15")
    expect(screen.getByRole("button", { name: en.cleanup.settings.custom })).toHaveAttribute("aria-pressed", "true")

    // Points libérés : le total doit retomber sur 100 avant d'enregistrer.
    fireEvent.change(slider("series"), { target: { value: "0" } })
    expect(screen.getByText("15 points left to share")).toBeInTheDocument()
    expect(save()).toBeDisabled()

    // Un autre critère ne prend que ce qui reste, sans rien retirer aux autres.
    fireEvent.change(slider("disinterest"), { target: { value: "90" } })
    expect(slider("disinterest")).toHaveValue("50")
    expect(screen.getByText(en.cleanup.settings.budgetFull)).toBeInTheDocument()

    await user.click(save())
    await waitFor(() =>
      expect(saveCleanupSettings).toHaveBeenCalledWith({
        ...BALANCED,
        preset: null,
        weights: { disinterest: 50, potential: 35, age: 15, series: 0 },
      }),
    )
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

  it("keeps its filters in the address, and reads them back", async () => {
    const user = userEvent.setup()
    renderPage("/cleanup?type=series&sort=space&protected=1&page=2")

    await screen.findByText("Forgotten")
    expect(getCleanupCandidates).toHaveBeenLastCalledWith(
      expect.objectContaining({ media_type: "series", sort: "space", include_protected: true, page: 2 }),
    )

    await user.click(screen.getByRole("switch", { name: en.cleanup.showProtected }))
    // Un filtre changé ramène à la première page ; les valeurs par défaut sortent de l'adresse.
    expect(screen.getByTestId("location")).toHaveTextContent("?type=series&sort=space")
  })

  it("switches to space and forecasts without losing the assistant filters", async () => {
    const user = userEvent.setup()
    renderPage("/cleanup?type=movie")
    await screen.findByText("Forgotten")

    await user.click(screen.getByRole("button", { name: en.cleanup.spaceSection }))

    expect(await screen.findByRole("heading", { name: en.forecast.title })).toBeInTheDocument()
    expect(screen.queryByText("Forgotten")).not.toBeInTheDocument()
    expect(screen.getByTestId("location")).toHaveTextContent("?type=movie&section=space")

    await user.click(screen.getByRole("button", { name: en.cleanup.title }))
    expect(await screen.findByText("Forgotten")).toBeInTheDocument()
    expect(screen.getByTestId("location")).toHaveTextContent("?type=movie")
  })

  it("keeps the selection when the user comes back from a media page", async () => {
    const user = userEvent.setup()
    const first = renderPage()
    await user.click(await screen.findByRole("checkbox", { name: "Select Forgotten" }))
    first.unmount()

    renderPage()

    expect(await screen.findByRole("checkbox", { name: "Select Forgotten" })).toBeChecked()
    expect(screen.getByText("1 media selected · 4.0 GB")).toBeInTheDocument()
  })

  it("selects every deletable media of the page at once", async () => {
    const user = userEvent.setup()
    renderPage("/cleanup?protected=1")
    await screen.findByText("Loved")

    await user.click(screen.getByRole("button", { name: en.cleanup.selectPage }))

    expect(screen.getByText("2 media selected · 6.0 GB")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: en.cleanup.unselectPage }))
    expect(screen.queryByText(/media selected/)).not.toBeInTheDocument()
  })
})
