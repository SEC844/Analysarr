import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { LinkToArrDialog } from "@/components/media/link-to-arr-dialog"
import { IgnoredSection } from "@/components/settings/ignored-section"
import { en } from "@/i18n/en"
import { createIgnore, deleteIgnore, getArrLinkPreview, listIgnores } from "@/lib/api"
import { renderWithProviders } from "@/test/render"
import type { MediaDetail } from "@/types/media"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  createIgnore: vi.fn(),
  deleteIgnore: vi.fn(),
  getArrLinkPreview: vi.fn(),
  listIgnores: vi.fn(),
}))

describe("IgnoredSection", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(deleteIgnore).mockResolvedValue(undefined)
    vi.mocked(listIgnores).mockResolvedValue([
      {
        id: 1,
        kind: "status",
        label: "Non suivi",
        status: "manquant_arr",
        media_title: "Film maison",
        media_type: "movie",
        media_id: 9,
        present: true,
        note: "Vidéo de famille",
        created_at: "2026-09-26T10:00:00",
      },
      {
        id: 2,
        kind: "torrent",
        label: "Matrix.1999.720p",
        status: null,
        media_title: "Matrix",
        media_type: "movie",
        media_id: null,
        present: false,
        note: null,
        created_at: "2026-09-25T10:00:00",
      },
    ])
  })

  it("lists every ignored item with its media and note", async () => {
    renderWithProviders(
      <MemoryRouter>
        <IgnoredSection />
      </MemoryRouter>,
    )

    expect(await screen.findByText(en.status.manquant_arr)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Film maison" })).toHaveAttribute("href", "/media/9")
    expect(screen.getByText("Vidéo de famille")).toBeInTheDocument()
    // Média disparu : plus de lien, et la mention qu'il est introuvable.
    expect(screen.queryByRole("link", { name: "Matrix" })).not.toBeInTheDocument()
    expect(screen.getByText(en.ignore.absent)).toBeInTheDocument()
  })

  it("stops ignoring an item", async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <MemoryRouter>
        <IgnoredSection />
      </MemoryRouter>,
    )

    const [first] = await screen.findAllByRole("button", { name: en.ignore.unignore })
    await user.click(first!)

    await waitFor(() => expect(deleteIgnore).toHaveBeenCalledWith(1))
  })
})

describe("LinkToArrDialog without any match", () => {
  const MEDIA = { id: 9, media_type: "movie", title: "Film maison" } as MediaDetail

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(createIgnore).mockResolvedValue(undefined)
    vi.mocked(getArrLinkPreview).mockResolvedValue({
      service: "radarr",
      instance_name: "Radarr",
      candidates: [],
      folders: [],
      suggested_folder: null,
      quality_profiles: [],
      suggested_profile: null,
    })
  })

  it("offers to hide the Untracked alert of this media", async () => {
    const user = userEvent.setup()
    renderWithProviders(<LinkToArrDialog media={MEDIA} />)

    await user.click(screen.getByRole("button", { name: en.linkArr.buttonMovie }))
    const mute = await screen.findByRole("button", {
      name: en.ignore.muteUntracked.replace("{status}", en.status.manquant_arr),
    })
    await user.click(mute)
    await user.click(screen.getByRole("button", { name: en.ignore.confirm }))

    await waitFor(() =>
      expect(createIgnore).toHaveBeenCalledWith({ media_id: 9, kind: "status", statuses: ["manquant_arr"], note: undefined }),
    )
  })
})
