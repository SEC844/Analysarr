import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ScheduleCard } from "@/components/settings/schedule-card"
import { en } from "@/i18n/en"
import { renderWithProviders } from "@/test/render"
import type { ScheduleMode } from "@/types/settings"

function renderCard(mode: ScheduleMode, intervalMinutes: number | null = 60) {
  const handlers = {
    onEnabledChange: vi.fn(),
    onIntervalMinutesChange: vi.fn(),
    onModeChange: vi.fn(),
    onNightlyHourChange: vi.fn(),
  }
  renderWithProviders(
    <ScheduleCard enabled intervalMinutes={intervalMinutes} mode={mode} nightlyHour={4} {...handlers} />,
  )
  return handlers
}

describe("ScheduleCard", () => {
  it("switches to once a night and keeps the interval for a way back", async () => {
    const user = userEvent.setup()
    const handlers = renderCard("interval")

    await user.click(screen.getByRole("combobox", { name: en.schedule.frequency }))
    await user.click(await screen.findByRole("option", { name: en.schedule.presets.nightly }))

    expect(handlers.onModeChange).toHaveBeenCalledWith("nightly")
    expect(handlers.onIntervalMinutesChange).not.toHaveBeenCalled()
  })

  it("asks for the hour only in nightly mode", async () => {
    const user = userEvent.setup()
    const handlers = renderCard("nightly")

    expect(screen.getByRole("combobox", { name: en.schedule.frequency })).toHaveTextContent(en.schedule.presets.nightly)
    await user.click(screen.getByRole("combobox", { name: en.schedule.nightlyHour }))
    await user.click(await screen.findByRole("option", { name: "3" }))

    expect(handlers.onNightlyHourChange).toHaveBeenCalledWith(3)
  })

  it("going back to an interval leaves the nightly mode", async () => {
    const user = userEvent.setup()
    const handlers = renderCard("nightly")

    await user.click(screen.getByRole("combobox", { name: en.schedule.frequency }))
    await user.click(await screen.findByRole("option", { name: en.schedule.presets.every6h }))

    expect(handlers.onModeChange).toHaveBeenCalledWith("interval")
    expect(handlers.onIntervalMinutesChange).toHaveBeenCalledWith(360)
  })
})
