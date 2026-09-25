import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { SeerCard } from "@/components/settings/seer-card"
import { en } from "@/i18n/en"
import { testConnection } from "@/lib/api"
import { renderWithProviders } from "@/test/render"
import type { RequestManager } from "@/i18n"

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  testConnection: vi.fn(),
}))

function renderCard(kind: RequestManager) {
  renderWithProviders(
    <SeerCard
      enabled
      onEnabledChange={vi.fn()}
      kind={kind}
      onKindChange={vi.fn()}
      url="http://requests:1234"
      onUrlChange={vi.fn()}
      apiKey="key"
      onApiKeyChange={vi.fn()}
      apiKeySet={false}
    />,
  )
}

describe("SeerCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(testConnection).mockResolvedValue({ success: true, message: "ok" })
  })

  it("names the chosen request manager and tests it as such", async () => {
    renderCard("ombi")

    expect(screen.getByLabelText("Ombi URL")).toHaveAttribute("placeholder", "http://ombi:3579")
    expect(screen.getByText(en.seer.apiKeyHelp.ombi)).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole("button", { name: en.common.testConnection }))

    expect(testConnection).toHaveBeenCalledWith("seer", {
      url: "http://requests:1234",
      api_key: "key",
      request_manager: "ombi",
    })
  })

  it("keeps Seer as it was", () => {
    renderCard("seer")

    expect(screen.getByLabelText("Seer URL")).toHaveAttribute("placeholder", "http://seerr:5055")
    expect(screen.getByText(en.seer.apiKeyHelp.seer)).toBeInTheDocument()
  })
})
