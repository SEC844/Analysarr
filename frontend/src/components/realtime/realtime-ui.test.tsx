import { renderHook, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { AnalysarrAddressCard } from "@/components/realtime/analysarr-address-card"
import { ArrWebhooksCard } from "@/components/realtime/arr-webhooks-card"
import { RealtimeIndicator } from "@/components/realtime/realtime-indicator"
import { useDetectAnalysarrAddress } from "@/hooks/use-realtime"
import { en } from "@/i18n/en"
import {
  getRealtimeStatus,
  getWebhooks,
  retryWebhook,
  saveAnalysarrAddress,
  setWebhooksEnabled,
  testWebhook,
} from "@/lib/api"
import { createWrapper, renderWithProviders } from "@/test/render"
import type { RealtimeStatus, WebhooksRead } from "@/types/realtime"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getRealtimeStatus: vi.fn(),
  getWebhooks: vi.fn(),
  retryWebhook: vi.fn(),
  saveAnalysarrAddress: vi.fn(),
  setWebhooksEnabled: vi.fn(),
  testWebhook: vi.fn(),
}))

const HOOK = { error: null, url: null, polling: "active", polling_error: null } as const

const WEBHOOKS: WebhooksRead = {
  analysarr_url: "http://analysarr:1818",
  realtime_enabled: true,
  enabled: true,
  webhooks: [
    { ...HOOK, service: "sonarr", instance_id: 0, name: "Sonarr", state: "connected", url: "http://a/w" },
    { ...HOOK, service: "sonarr", instance_id: 2, name: "Sonarr 4K", state: "error", error: "Sonarr 4K injoignable" },
    { ...HOOK, service: "radarr", instance_id: 0, name: "Radarr", state: "pending" },
  ],
}

const STATUS: RealtimeStatus = { enabled: true, active: true, address_set: true, sources: [] }

function inRouter(ui: React.ReactElement) {
  return renderWithProviders(<MemoryRouter>{ui}</MemoryRouter>)
}

describe("realtime UI", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getWebhooks).mockResolvedValue(WEBHOOKS)
    vi.mocked(getRealtimeStatus).mockResolvedValue(STATUS)
    vi.mocked(retryWebhook).mockResolvedValue(WEBHOOKS)
    vi.mocked(testWebhook).mockResolvedValue(undefined)
    vi.mocked(saveAnalysarrAddress).mockResolvedValue({ ...WEBHOOKS, analysarr_url: "http://analysarr.lan:1818" })
  })

  it("shows each instance of the service with its webhook state", async () => {
    const user = userEvent.setup()
    inRouter(<ArrWebhooksCard service="sonarr" />)

    expect(await screen.findByText("Sonarr 4K injoignable")).toBeInTheDocument()
    expect(screen.queryByText("Radarr")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: en.realtime.webhooks.test }))
    await waitFor(() => expect(testWebhook).toHaveBeenCalledWith("sonarr", 0))
    await user.click(screen.getByRole("button", { name: en.realtime.webhooks.retry }))
    await waitFor(() => expect(retryWebhook).toHaveBeenCalledWith("sonarr", 2))
  })

  it("points to the address when it is missing", async () => {
    vi.mocked(getWebhooks).mockResolvedValue({
      ...WEBHOOKS,
      analysarr_url: "",
      webhooks: [{ ...HOOK, service: "radarr", instance_id: 0, name: "Radarr", state: "no_address" }],
    })
    inRouter(<ArrWebhooksCard service="radarr" />)

    const link = await screen.findByRole("link", { name: en.realtime.webhooks.openAddress })
    expect(link).toHaveAttribute("href", "/settings?section=application")
  })

  it("lets the user refuse webhooks and shows the read-only following instead", async () => {
    const user = userEvent.setup()
    const disabled: WebhooksRead = {
      ...WEBHOOKS,
      enabled: false,
      webhooks: [{ ...HOOK, service: "sonarr", instance_id: 0, name: "Sonarr", state: "disabled" }],
    }
    vi.mocked(getWebhooks).mockResolvedValue({ ...WEBHOOKS, webhooks: WEBHOOKS.webhooks.slice(0, 1) })
    vi.mocked(setWebhooksEnabled).mockResolvedValue(disabled)
    inRouter(<ArrWebhooksCard service="sonarr" />)

    const toggle = await screen.findByRole("switch", { name: "Create a webhook in Sonarr" })
    expect(toggle).toBeChecked()
    // Webhook branché : le suivi en lecture seule reste discret.
    expect(screen.queryByText(en.realtime.webhooks.polling.active)).not.toBeInTheDocument()

    await user.click(toggle)

    await waitFor(() => expect(setWebhooksEnabled).toHaveBeenCalledWith(false, expect.anything()))
    expect(await screen.findByText(`· ${en.realtime.webhooks.states.disabled}`)).toBeInTheDocument()
    expect(screen.getByText(en.realtime.webhooks.polling.active)).toBeInTheDocument()
    expect(screen.getByRole("switch", { name: "Create a webhook in Sonarr" })).not.toBeChecked()
  })

  it("points to the schedule when real time is off", async () => {
    vi.mocked(getWebhooks).mockResolvedValue({ ...WEBHOOKS, realtime_enabled: false })
    inRouter(<ArrWebhooksCard service="sonarr" />)

    const link = await screen.findByRole("link", { name: en.realtime.webhooks.openSchedule })
    expect(link).toHaveAttribute("href", "/settings?section=schedule")
    expect(screen.queryByRole("switch")).not.toBeInTheDocument()
  })

  it("shows nothing in the header in scans-only mode", async () => {
    vi.mocked(getRealtimeStatus).mockResolvedValue({ ...STATUS, enabled: false, active: false })
    const { container } = inRouter(<RealtimeIndicator />)

    await waitFor(() => expect(getRealtimeStatus).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it("saves a corrected address", async () => {
    const user = userEvent.setup()
    inRouter(<AnalysarrAddressCard />)
    const input = await screen.findByLabelText(en.realtime.address.label)
    expect(screen.getByRole("button", { name: en.common.save })).toBeDisabled()

    await user.clear(input)
    await user.type(input, "http://analysarr.lan:1818")
    await user.click(screen.getByRole("button", { name: en.common.save }))

    await waitFor(() => expect(saveAnalysarrAddress).toHaveBeenCalledWith("http://analysarr.lan:1818", false))
  })

  it("leads straight to the failing service", async () => {
    vi.mocked(getRealtimeStatus).mockResolvedValue({
      ...STATUS,
      sources: [
        { key: "requests", kind: "requests", state: "error", error: "HTTP 401", last_check_at: null, last_event_at: null },
      ],
    })
    inRouter(<RealtimeIndicator />)

    expect(await screen.findByRole("link")).toHaveAttribute("href", "/settings?section=seer")
  })

  it("stays a plain status while everything works", async () => {
    vi.mocked(getRealtimeStatus).mockResolvedValue({
      ...STATUS,
      sources: [{ key: "torrents", kind: "torrents", state: "active", error: null, last_check_at: null, last_event_at: null }],
    })
    inRouter(<RealtimeIndicator />)

    expect(await screen.findByRole("img", { name: en.realtime.indicator.ok })).toBeInTheDocument()
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
  })

  it("proposes this browser's address once when none is known", async () => {
    vi.mocked(getRealtimeStatus).mockResolvedValue({ ...STATUS, address_set: false })
    const { rerender } = renderHook(() => useDetectAnalysarrAddress(), { wrapper: createWrapper() })

    await waitFor(() => expect(saveAnalysarrAddress).toHaveBeenCalledWith(window.location.origin, true))
    rerender()
    expect(saveAnalysarrAddress).toHaveBeenCalledTimes(1)
  })

  it("never touches a known address", async () => {
    renderHook(() => useDetectAnalysarrAddress(), { wrapper: createWrapper() })
    await waitFor(() => expect(getRealtimeStatus).toHaveBeenCalled())
    expect(saveAnalysarrAddress).not.toHaveBeenCalled()
  })
})
