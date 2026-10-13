import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { toast } from "sonner"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { NotificationsSection } from "@/components/settings/notifications-section"
import { en } from "@/i18n/en"
import { listNotificationChannels, sendWeeklySummary } from "@/lib/api"
import { renderWithProviders } from "@/test/render"
import { NOTIFICATION_EVENTS } from "@/types/notifications"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listNotificationChannels: vi.fn(),
  sendWeeklySummary: vi.fn(),
}))

async function sendNow() {
  const user = userEvent.setup()
  renderWithProviders(<NotificationsSection />)
  await user.click(await screen.findByRole("button", { name: en.notifications.weekly.sendNow }))
}

describe("NotificationsSection — weekly summary", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listNotificationChannels).mockResolvedValue([])
  })

  it("is an event channels can subscribe to", () => {
    expect(NOTIFICATION_EVENTS).toContain("weekly_summary")
  })

  it("sends it now and reports the channels reached", async () => {
    vi.mocked(sendWeeklySummary).mockResolvedValue({ channels: [{ name: "Discord", error: null }], skipped: null })
    await sendNow()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Summary sent to 1 channel."))
  })

  it("says why nothing was sent", async () => {
    vi.mocked(sendWeeklySummary).mockResolvedValue({ channels: [], skipped: "no_subscriber" })
    await sendNow()
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith(en.notifications.weekly.noSubscriber))
  })

  it("names the channels that failed", async () => {
    vi.mocked(sendWeeklySummary).mockResolvedValue({
      channels: [
        { name: "Discord", error: null },
        { name: "ntfy", error: "HTTP 500" },
      ],
      skipped: null,
    })
    await sendNow()
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Sending failed on ntfy."))
  })
})
