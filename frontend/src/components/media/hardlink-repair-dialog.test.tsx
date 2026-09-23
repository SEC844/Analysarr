import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { toast } from "sonner"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { HardlinkRepairDialog } from "@/components/media/hardlink-repair-dialog"
import { en } from "@/i18n/en"
import { hardlinkRepairExecute, hardlinkRepairPreview } from "@/lib/api"
import { footerCloseButton } from "@/test/dialog"
import { renderWithProviders } from "@/test/render"
import type { HardlinkRepairItem } from "@/types/media"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  hardlinkRepairPreview: vi.fn(),
  hardlinkRepairExecute: vi.fn(),
}))

const MEDIA_ID = 12
const ITEM: HardlinkRepairItem = {
  media_file_id: 3,
  episode_label: "S01E01",
  torrent_id: 9,
  torrent_name: "Dark.S01.1080p",
  direction: "torrent_to_library",
  source_path: "/data/torrents/tv/Dark.S01/Dark.S01E01.mkv",
  target_path: "/data/media/tv/Dark/Season 01/Dark.S01E01.mkv",
  target_exists: true,
  size: 1100,
}

async function openDialog() {
  const user = userEvent.setup()
  renderWithProviders(<HardlinkRepairDialog mediaId={MEDIA_ID} />)
  await user.click(screen.getByRole("button", { name: en.repair.button }))
  return user
}

const queryConfirm = () => screen.queryByRole("button", { name: en.repair.confirm })
const getConfirm = () => screen.getByRole("button", { name: en.repair.confirm })

describe("HardlinkRepairDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(hardlinkRepairPreview).mockResolvedValue({ items: [ITEM], unmatched_torrents: [] })
    vi.mocked(hardlinkRepairExecute).mockResolvedValue({ steps: [] })
  })

  it("shows the preview of every file to relink before any action", async () => {
    await openDialog()

    expect(await screen.findByText(ITEM.target_path)).toBeInTheDocument()
    expect(getConfirm()).toBeEnabled()
    expect(hardlinkRepairPreview).toHaveBeenCalledWith(MEDIA_ID)
    expect(hardlinkRepairExecute).not.toHaveBeenCalled()
  })

  it("offers no confirmation while the preview is loading or empty", async () => {
    vi.mocked(hardlinkRepairPreview).mockResolvedValue({ items: [], unmatched_torrents: ["Dark.S01.720p"] })
    await openDialog()

    expect(await screen.findByText(en.repair.nothing)).toBeInTheDocument()
    expect(queryConfirm()).not.toBeInTheDocument()
  })

  it("never calls the API when the dialog is closed", async () => {
    const user = await openDialog()
    await screen.findByText(ITEM.target_path)

    await user.click(footerCloseButton())

    await waitFor(() => expect(screen.queryByText(en.repair.title)).not.toBeInTheDocument())
    expect(hardlinkRepairExecute).not.toHaveBeenCalled()
  })

  it("repairs this media only once confirmed", async () => {
    vi.mocked(hardlinkRepairExecute).mockResolvedValue({
      steps: [{ media_file_id: 3, label: "S01E01", success: true, error: null, used_symlink: false }],
    })
    const user = await openDialog()
    await screen.findByText(ITEM.target_path)

    await user.click(getConfirm())

    await waitFor(() => expect(hardlinkRepairExecute).toHaveBeenCalledTimes(1))
    expect(hardlinkRepairExecute).toHaveBeenCalledWith(MEDIA_ID)
    expect(queryConfirm()).not.toBeInTheDocument() // récapitulatif affiché
  })

  it("shows the API error and keeps the dialog open", async () => {
    vi.mocked(hardlinkRepairExecute).mockRejectedValue(new Error("Volume /data non monté"))
    const user = await openDialog()
    await screen.findByText(ITEM.target_path)

    await user.click(getConfirm())

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Volume /data non monté"))
    expect(screen.getByText(en.repair.title)).toBeInTheDocument()
    expect(getConfirm()).toBeEnabled()
  })
})
