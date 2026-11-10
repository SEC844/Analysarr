import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { PathDiagnosticsPanel } from "@/components/settings/path-diagnostics-panel"
import { en } from "@/i18n/en"
import { getPathDiagnostics } from "@/lib/api"
import { renderWithProviders } from "@/test/render"
import type { DiagnosticsResult } from "@/types/diagnostics"

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getPathDiagnostics: vi.fn(),
}))

const ALL_GOOD = { total: 1, resolved: 1, unresolved_samples: [], common_unresolved_prefix: null }

function result(separate: boolean): DiagnosticsResult {
  return { qbittorrent: ALL_GOOD, emby: ALL_GOOD, separate_mounts: separate }
}

async function run() {
  const user = userEvent.setup()
  renderWithProviders(<PathDiagnosticsPanel />)
  await user.click(screen.getByRole("button", { name: en.diagnostics.run }))
}

describe("PathDiagnosticsPanel", () => {
  beforeEach(() => vi.clearAllMocks())

  it("warns when the library and the downloads are on separate file systems", async () => {
    vi.mocked(getPathDiagnostics).mockResolvedValue(result(true))
    await run()

    expect(await screen.findByText("**/.analysarr-trash/**")).toBeInTheDocument()
  })

  it("stays quiet on a single file system", async () => {
    vi.mocked(getPathDiagnostics).mockResolvedValue(result(false))
    await run()

    expect((await screen.findAllByText(/qBittorrent/)).length).toBeGreaterThan(0)
    expect(screen.queryByText("**/.analysarr-trash/**")).not.toBeInTheDocument()
  })
})
