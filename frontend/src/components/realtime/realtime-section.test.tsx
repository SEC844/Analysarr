import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { RealtimeIndicator } from "@/components/realtime/realtime-indicator"
import { RealtimeSection } from "@/components/realtime/realtime-section"
import { en } from "@/i18n/en"
import {
  getRealtimeSettings,
  getRealtimeStatus,
  previewWebhook,
  registerWebhook,
  saveRealtimeSettings,
  scheduleNightlyScan,
  testWebhook,
  unregisterWebhook,
} from "@/lib/api"
import { renderWithProviders } from "@/test/render"
import type { RealtimeSettings, RealtimeStatus } from "@/types/realtime"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getRealtimeSettings: vi.fn(),
  getRealtimeStatus: vi.fn(),
  saveRealtimeSettings: vi.fn(),
  previewWebhook: vi.fn(),
  registerWebhook: vi.fn(),
  testWebhook: vi.fn(),
  unregisterWebhook: vi.fn(),
  scheduleNightlyScan: vi.fn(),
}))

const SETTINGS: RealtimeSettings = {
  debounce_seconds: 3,
  torrents_enabled: false,
  torrents_interval: 3,
  torrents_available: true,
  media_server_enabled: false,
  media_server_interval: 5,
  media_server_available: false,
  analysarr_url: "",
  webhooks: [
    { service: "sonarr", instance_id: 0, name: "Sonarr", connected: false, url: null },
    { service: "radarr", instance_id: 2, name: "Radarr 4K", connected: true, url: "http://a/api/webhooks/radarr/2" },
  ],
  debounce_bounds: [1, 60],
  torrent_interval_bounds: [2, 300],
  media_server_interval_bounds: [3, 300],
}

const STATUS: RealtimeStatus = {
  active: true,
  sources: [
    {
      key: "webhook:radarr:2",
      kind: "webhook",
      state: "active",
      last_event_at: "2026-10-05T10:00:00",
      last_check_at: "2026-10-05T10:00:00",
      error: null,
    },
    { key: "torrents", kind: "torrents", state: "error", last_event_at: null, last_check_at: null, error: "HTTP 403" },
  ],
  reconciliation: { enabled: true, mode: "interval", interval_minutes: 60, nightly_hour: 4 },
}

function renderSection() {
  renderWithProviders(
    <MemoryRouter>
      <RealtimeSection />
    </MemoryRouter>,
  )
}

describe("RealtimeSection", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getRealtimeSettings).mockResolvedValue(SETTINGS)
    vi.mocked(getRealtimeStatus).mockResolvedValue(STATUS)
    vi.mocked(saveRealtimeSettings).mockImplementation(async (payload) => ({
      ...SETTINGS,
      debounce_seconds: payload.debounce_seconds,
      torrents_enabled: payload.torrents_enabled,
      torrents_interval: payload.torrents_interval,
    }))
    vi.mocked(previewWebhook).mockResolvedValue({
      name: "Analysarr",
      url: "http://analysarr:1818/api/webhooks/sonarr/0",
      events: ["onDownload", "onSeriesDelete"],
    })
    vi.mocked(registerWebhook).mockResolvedValue(SETTINGS)
    vi.mocked(testWebhook).mockResolvedValue(undefined)
    vi.mocked(unregisterWebhook).mockResolvedValue(SETTINGS)
    vi.mocked(scheduleNightlyScan).mockResolvedValue({
      ...STATUS,
      reconciliation: { enabled: true, mode: "nightly", interval_minutes: 60, nightly_hour: 4 },
    })
  })

  it("shows each source with its state and error", async () => {
    renderSection()

    expect(await screen.findByText(/Radarr 4K webhook/)).toBeInTheDocument()
    expect(screen.getByText("HTTP 403")).toBeInTheDocument()
    expect(screen.getAllByText(/Error/).length).toBeGreaterThan(0)
  })

  it("connects a webhook only after showing what will be created", async () => {
    const user = userEvent.setup()
    renderSection()
    const address = await screen.findByLabelText(en.realtime.webhooks.address)
    await user.clear(address)
    await user.type(address, "http://analysarr:1818")

    await user.click(screen.getByRole("button", { name: en.realtime.webhooks.connect }))

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText("http://analysarr:1818/api/webhooks/sonarr/0")).toBeInTheDocument()
    expect(within(dialog).getByText("onSeriesDelete")).toBeInTheDocument()
    expect(registerWebhook).not.toHaveBeenCalled()
    expect(previewWebhook).toHaveBeenCalledWith("sonarr", 0, "http://analysarr:1818")

    await user.click(within(dialog).getByRole("button", { name: en.realtime.webhooks.confirm }))

    await waitFor(() => expect(registerWebhook).toHaveBeenCalledWith("sonarr", 0, "http://analysarr:1818"))
  })

  it("never creates anything when the preview is cancelled", async () => {
    const user = userEvent.setup()
    renderSection()
    await user.click(await screen.findByRole("button", { name: en.realtime.webhooks.connect }))
    const dialog = await screen.findByRole("dialog")

    await user.click(within(dialog).getByRole("button", { name: en.common.cancel }))

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    expect(registerWebhook).not.toHaveBeenCalled()
  })

  it("tests a connected webhook and disconnects it in two steps", async () => {
    const user = userEvent.setup()
    renderSection()

    await user.click(await screen.findByRole("button", { name: en.realtime.webhooks.test }))
    await waitFor(() => expect(testWebhook).toHaveBeenCalledWith("radarr", 2))

    await user.click(screen.getByRole("button", { name: en.realtime.webhooks.disconnect }))
    expect(unregisterWebhook).not.toHaveBeenCalled()
    await user.click(screen.getByRole("button", { name: en.realtime.webhooks.disconnectConfirm }))
    await waitFor(() => expect(unregisterWebhook).toHaveBeenCalledWith("radarr", 2))
  })

  it("turns the torrent client on and refuses an interval out of bounds", async () => {
    const user = userEvent.setup()
    renderSection()

    await user.click(await screen.findByRole("switch", { name: en.realtime.torrents.enable }))
    const interval = screen.getByLabelText(en.realtime.interval)
    await user.clear(interval)
    await user.type(interval, "1")
    expect(screen.getByRole("button", { name: en.realtime.save })).toBeDisabled()

    await user.clear(interval)
    await user.type(interval, "5")
    await user.click(screen.getByRole("button", { name: en.realtime.save }))

    await waitFor(() =>
      expect(saveRealtimeSettings).toHaveBeenCalledWith({
        debounce_seconds: 3,
        torrents_enabled: true,
        torrents_interval: 5,
        media_server_enabled: false,
        media_server_interval: 5,
      }),
    )
  })

  it("cannot follow a media server that is not configured", async () => {
    renderSection()

    const mediaServer = await screen.findByRole("switch", { name: "Follow Emby" })

    expect(mediaServer).toHaveAttribute("data-disabled")
    expect(screen.getByText("Emby not configured.")).toBeInTheDocument()
  })

  it("offers the nightly reconciliation scan without imposing it", async () => {
    const user = userEvent.setup()
    renderSection()

    await user.click(
      await screen.findByRole("button", { name: en.realtime.reconciliation.suggest.replace("{hour}", "4") }),
    )

    await waitFor(() => expect(scheduleNightlyScan).toHaveBeenCalledWith(4))
  })
})

describe("RealtimeIndicator", () => {
  beforeEach(() => vi.clearAllMocks())

  it("stays hidden while real time is off", async () => {
    vi.mocked(getRealtimeStatus).mockResolvedValue({ ...STATUS, active: false, sources: [] })
    renderWithProviders(
      <MemoryRouter>
        <RealtimeIndicator />
      </MemoryRouter>,
    )
    await waitFor(() => expect(getRealtimeStatus).toHaveBeenCalled())
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
  })

  it("links to the settings and flags a failing source", async () => {
    vi.mocked(getRealtimeStatus).mockResolvedValue(STATUS)
    renderWithProviders(
      <MemoryRouter>
        <RealtimeIndicator />
      </MemoryRouter>,
    )
    const link = await screen.findByRole("link")
    expect(link).toHaveAttribute("href", "/settings?section=realtime")
    expect(screen.getByRole("status", { name: en.realtime.indicator.error })).toBeInTheDocument()
  })
})
