import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { AutomationsSection } from "@/components/settings/automations-section"
import { en } from "@/i18n/en"
import { createAutomation, getAutomationGuard, listAutomations } from "@/lib/api"
import { renderWithProviders } from "@/test/render"
import type { Automation } from "@/types/automations"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listAutomations: vi.fn(),
  getAutomationGuard: vi.fn(),
  createAutomation: vi.fn(),
}))

const GB = 1024 ** 3

async function newCleanupRule() {
  const user = userEvent.setup()
  renderWithProviders(<AutomationsSection />)
  await user.click(await screen.findByRole("button", { name: en.automations.add }))
  await user.type(screen.getByLabelText(en.automations.name), "Grand ménage")
  await user.click(screen.getByRole("combobox", { name: en.automations.trigger }))
  await user.click(await screen.findByRole("option", { name: en.automations.triggers.cleanup_candidate }))
  return user
}

describe("AutomationsSection — cleanup candidates", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listAutomations).mockResolvedValue([])
    vi.mocked(getAutomationGuard).mockResolvedValue({
      percent: 20,
      min_percent: 5,
      active: false,
      paused: false,
      paused_at: null,
      status: null,
      previous: null,
      current: null,
      total: null,
      changed_percent: null,
    })
    vi.mocked(createAutomation).mockImplementation(
      async (payload) => ({ ...payload, id: 1, last_run_at: null, last_run_count: 0 }) as Automation,
    )
  })

  it("proposes careful values and deletes whole media at most ten at a time", async () => {
    await newCleanupRule()

    expect(screen.getByRole("combobox", { name: en.automations.action })).toHaveTextContent(
      en.automations.actions.delete_media,
    )
    expect(screen.getByLabelText(en.automations.minScore)).toHaveValue(80)
    expect(screen.getByLabelText(en.automations.minFreed)).toHaveValue(10)
    expect(screen.getByLabelText(en.automations.maxActions)).toHaveAttribute("max", "10")
    expect(screen.getByText(en.automations.conditionsRequired)).toBeInTheDocument()
  })

  it("refuses a score under the floor, then saves the exact rule in simulation", async () => {
    const user = await newCleanupRule()
    const score = screen.getByLabelText(en.automations.minScore)

    await user.clear(score)
    await user.type(score, "40")
    expect(screen.getByText(/A minimum score of at least 50/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: en.common.save })).toBeDisabled()

    await user.clear(score)
    await user.type(score, "75")
    await user.click(screen.getByRole("button", { name: en.common.save }))

    await waitFor(() =>
      expect(createAutomation).toHaveBeenCalledWith({
        name: "Grand ménage",
        enabled: true,
        trigger: "cleanup_candidate",
        action: "delete_media",
        conditions: {
          media_types: [],
          min_seed_days: null,
          min_ratio: null,
          min_reclaimable_bytes: 10 * GB,
          min_score: 75,
        },
        max_actions: 5,
        dry_run: true,
      }),
    )
  })

  it("never lets a missing space condition through", async () => {
    const user = await newCleanupRule()
    await user.clear(screen.getByLabelText(en.automations.minFreed))
    expect(screen.getByRole("button", { name: en.common.save })).toBeDisabled()
    expect(createAutomation).not.toHaveBeenCalled()
  })
})
