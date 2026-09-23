import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { toast } from "sonner"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { DeleteCascadeDialog } from "@/components/media/delete-cascade-dialog"
import { en } from "@/i18n/en"
import { deleteExecute, deletePreview } from "@/lib/api"
import { footerCloseButton } from "@/test/dialog"
import { renderWithProviders } from "@/test/render"
import type { DeletePreview } from "@/types/media"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  deletePreview: vi.fn(),
  deleteExecute: vi.fn(),
}))

const MEDIA_ID = 7
const DUPLICATE = { kind: "duplicate_file", label: "/data/media/movies/Inception/Inception.1080p.mkv", size: 2000 } as const
const ORPHAN = { kind: "orphan_torrent", label: "Inception.2010.720p", size: 1700 } as const
const PREVIEW: DeletePreview = { items: [DUPLICATE, ORPHAN], total_reclaimable_bytes: 3700 }

async function openDialog() {
  const user = userEvent.setup()
  renderWithProviders(<DeleteCascadeDialog mediaId={MEDIA_ID} />)
  await user.click(screen.getByRole("button", { name: en.cascade.button }))
  return user
}

const queryConfirm = () => screen.queryByRole("button", { name: en.common.confirmDelete })
const getConfirm = () => screen.getByRole("button", { name: en.common.confirmDelete })

describe("DeleteCascadeDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(deletePreview).mockResolvedValue(PREVIEW)
    vi.mocked(deleteExecute).mockResolvedValue({ steps: [] })
  })

  it("lists every file and torrent the cleanup will remove, with the space freed", async () => {
    await openDialog()

    expect(await screen.findByText(DUPLICATE.label)).toBeInTheDocument()
    expect(screen.getByText(ORPHAN.label)).toBeInTheDocument()
    expect(screen.getByText(en.cascade.kinds.duplicate_file)).toBeInTheDocument()
    expect(screen.getByText(en.cascade.kinds.orphan_torrent)).toBeInTheDocument()
    expect(deletePreview).toHaveBeenCalledWith(MEDIA_ID)
    expect(deleteExecute).not.toHaveBeenCalled()
  })

  it("offers no confirmation before the preview is known", async () => {
    vi.mocked(deletePreview).mockReturnValue(new Promise(() => {}))
    await openDialog()

    expect(await screen.findByText(en.common.previewing)).toBeInTheDocument()
    expect(queryConfirm()).not.toBeInTheDocument()
  })

  it("offers no confirmation when there is nothing to clean", async () => {
    vi.mocked(deletePreview).mockResolvedValue({ items: [], total_reclaimable_bytes: 0 })
    await openDialog()

    expect(await screen.findByText(en.cascade.nothing)).toBeInTheDocument()
    expect(queryConfirm()).not.toBeInTheDocument()
  })

  it("never calls the API when the dialog is closed", async () => {
    const user = await openDialog()
    await screen.findByText(DUPLICATE.label)

    await user.click(footerCloseButton())

    await waitFor(() => expect(screen.queryByText(en.cascade.title)).not.toBeInTheDocument())
    expect(deleteExecute).not.toHaveBeenCalled()
  })

  it("runs the cleanup of this media only once confirmed", async () => {
    vi.mocked(deleteExecute).mockResolvedValue({
      steps: [{ kind: "duplicate_file", label: DUPLICATE.label, success: true, error: null }],
    })
    const user = await openDialog()
    await screen.findByText(DUPLICATE.label)

    await user.click(getConfirm())

    await waitFor(() => expect(deleteExecute).toHaveBeenCalledTimes(1))
    expect(deleteExecute).toHaveBeenCalledWith(MEDIA_ID)
    expect(queryConfirm()).not.toBeInTheDocument() // récapitulatif affiché
  })

  it("shows the API error and keeps the dialog open", async () => {
    vi.mocked(deleteExecute).mockRejectedValue(new Error("Montage /data introuvable"))
    const user = await openDialog()
    await screen.findByText(DUPLICATE.label)

    await user.click(getConfirm())

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Montage /data introuvable"))
    expect(screen.getByText(en.cascade.title)).toBeInTheDocument()
    expect(getConfirm()).toBeEnabled()
  })
})
