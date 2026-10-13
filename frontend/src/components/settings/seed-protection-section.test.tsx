import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { SeedProtectionBanner } from "@/components/seed/seed-protection-banner"
import { SeedProtectionSection } from "@/components/settings/seed-protection-section"
import { en } from "@/i18n/en"
import { dismissSeedProtectionPrompt, getSeedProtection, saveSeedProtection } from "@/lib/api"
import { renderWithProviders } from "@/test/render"
import type { SeedProtection } from "@/types/seed"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getSeedProtection: vi.fn(),
  saveSeedProtection: vi.fn(),
  dismissSeedProtectionPrompt: vi.fn(),
}))

const EXISTING_INSTALL: SeedProtection = {
  enabled: false,
  private_min_days: 14,
  public_enabled: false,
  public_min_days: 3,
  tracker_rules: [],
  prompt: true,
  known_trackers: ["tracker.example.org"],
  min_days: 0,
  max_days: 365,
  max_ratio: 100,
  max_rules: 50,
}

function renderSection() {
  renderWithProviders(
    <MemoryRouter>
      <SeedProtectionSection />
    </MemoryRouter>,
  )
}

describe("SeedProtectionBanner", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getSeedProtection).mockResolvedValue(EXISTING_INSTALL)
    vi.mocked(saveSeedProtection).mockResolvedValue({ ...EXISTING_INSTALL, enabled: true, prompt: false })
    vi.mocked(dismissSeedProtectionPrompt).mockResolvedValue({ ...EXISTING_INSTALL, prompt: false })
  })

  it("offers the protection to an existing install and turns it on only when asked", async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <MemoryRouter>
        <SeedProtectionBanner />
      </MemoryRouter>,
    )

    expect(await screen.findByText(en.seed.prompt.title)).toBeInTheDocument()
    expect(saveSeedProtection).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: en.seed.prompt.enable }))

    await waitFor(() =>
      expect(saveSeedProtection).toHaveBeenCalledWith({
        enabled: true,
        private_min_days: 14,
        public_enabled: false,
        public_min_days: 3,
        tracker_rules: [],
      }),
    )
    await waitFor(() => expect(screen.queryByText(en.seed.prompt.title)).not.toBeInTheDocument())
  })

  it("can be dismissed without turning anything on", async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <MemoryRouter>
        <SeedProtectionBanner />
      </MemoryRouter>,
    )

    await user.click(await screen.findByRole("button", { name: en.seed.prompt.dismiss }))

    await waitFor(() => expect(dismissSeedProtectionPrompt).toHaveBeenCalled())
    expect(saveSeedProtection).not.toHaveBeenCalled()
  })

  it("stays hidden once the question is settled", async () => {
    vi.mocked(getSeedProtection).mockResolvedValue({ ...EXISTING_INSTALL, prompt: false })
    renderWithProviders(
      <MemoryRouter>
        <SeedProtectionBanner />
      </MemoryRouter>,
    )

    await waitFor(() => expect(getSeedProtection).toHaveBeenCalled())
    expect(screen.queryByText(en.seed.prompt.title)).not.toBeInTheDocument()
  })
})

describe("SeedProtectionSection", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getSeedProtection).mockResolvedValue({ ...EXISTING_INSTALL, enabled: true, prompt: false })
    vi.mocked(saveSeedProtection).mockImplementation(async (payload) => ({
      ...EXISTING_INSTALL,
      ...payload,
      prompt: false,
    }))
  })

  it("saves a tracker rule with its days and ratio, normalised", async () => {
    const user = userEvent.setup()
    renderSection()

    await user.click(await screen.findByRole("button", { name: en.seed.addRule }))
    await user.type(screen.getByLabelText(en.seed.domain), " Tracker.Example.ORG ")
    const days = ruleDaysInput()
    await user.clear(days)
    await user.type(days, "30")
    await user.type(screen.getByLabelText(en.seed.ratio), "1.5")
    await user.click(screen.getByRole("button", { name: en.seed.save }))

    await waitFor(() =>
      expect(saveSeedProtection).toHaveBeenCalledWith({
        enabled: true,
        private_min_days: 14,
        public_enabled: false,
        public_min_days: 3,
        tracker_rules: [{ domain: "tracker.example.org", min_days: 30, min_ratio: 1.5 }],
      }),
    )
  })

  it("refuses to save an invalid value", async () => {
    const user = userEvent.setup()
    renderSection()

    const privateDays = await screen.findByLabelText(en.seed.privateDays)
    await user.clear(privateDays)
    await user.type(privateDays, "400")

    expect(screen.getByRole("button", { name: en.seed.save })).toBeDisabled()
    expect(screen.getByRole("alert")).toBeInTheDocument()
  })

  it("refuses a tracker url instead of a domain", async () => {
    const user = userEvent.setup()
    renderSection()

    await user.click(await screen.findByRole("button", { name: en.seed.addRule }))
    await user.type(screen.getByLabelText(en.seed.domain), "https://tracker.example.org/announce?passkey=abc")

    expect(screen.getByRole("button", { name: en.seed.save })).toBeDisabled()
    expect(saveSeedProtection).not.toHaveBeenCalled()
  })

  it("asks for the public time only once public torrents are protected", async () => {
    const user = userEvent.setup()
    renderSection()

    const publicSwitch = await screen.findByRole("switch", { name: en.seed.publicEnabled })
    expect(screen.queryByLabelText(en.seed.publicDays)).not.toBeInTheDocument()

    await user.click(publicSwitch)

    expect(screen.getByLabelText(en.seed.publicDays)).toBeInTheDocument()
  })
})

// Champ « jours » de la première règle (libellé « jours » partagé avec
// l'unité affichée à côté des durées générales).
function ruleDaysInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>("input[id^='seed-rule-days-']")
  if (!input) throw new Error("aucune règle affichée")
  return input
}
