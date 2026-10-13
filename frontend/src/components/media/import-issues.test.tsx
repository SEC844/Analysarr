import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { toast } from "sonner"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ImportIssues } from "@/components/media/import-issues"
import { en } from "@/i18n/en"
import { retryImport } from "@/lib/api"
import { renderWithProviders } from "@/test/render"
import type { ImportIssueRead } from "@/types/media"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  retryImport: vi.fn(),
}))

const ISSUE: ImportIssueRead = {
  id: 1,
  kind: "import",
  title: "South Park S29E03 1080p WEB H264-MeGusta",
  state: "importBlocked",
  reason: "Episode has a TBA title and recently aired",
  size: 1000,
  episode_label: "S29E03",
  can_retry: true,
}

async function retry() {
  const user = userEvent.setup()
  renderWithProviders(<ImportIssues mediaId={4} issues={[ISSUE]} />)
  await user.click(screen.getByRole("button", { name: en.importIssues.retry }))
}

describe("ImportIssues", () => {
  beforeEach(() => vi.clearAllMocks())

  it("announces the imported files and shows each step", async () => {
    const label = `${ISSUE.title} — 1 fichier(s) importé(s)`
    vi.mocked(retryImport).mockResolvedValue({
      steps: [{ kind: "import_retry", label, success: true, error: null }],
      imported_files: 1,
    })

    await retry()

    expect(await screen.findByText(label)).toBeInTheDocument()
    expect(retryImport).toHaveBeenCalledWith(4)
    expect(toast.success).toHaveBeenCalledWith("1 file imported.")
  })

  it("says so when Sonarr/Radarr had already sorted it out", async () => {
    vi.mocked(retryImport).mockResolvedValue({
      steps: [{ kind: "import_retry", label: "South Park — plus aucun import bloqué", success: true, error: null }],
      imported_files: 0,
    })

    await retry()

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(en.importIssues.alreadyResolved))
    expect(toast.error).not.toHaveBeenCalled()
  })

  it("reports a refusal with its reason", async () => {
    const error = "Refusé par Sonarr/Radarr, à trancher dans son import manuel : Sample"
    vi.mocked(retryImport).mockResolvedValue({
      steps: [{ kind: "import_retry", label: ISSUE.title, success: false, error }],
      imported_files: 0,
    })

    await retry()

    expect(await screen.findByText(error)).toBeInTheDocument()
    expect(toast.error).toHaveBeenCalledWith(en.importIssues.nothingImported)
  })
})
