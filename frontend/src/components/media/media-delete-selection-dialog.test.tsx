import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { toast } from "sonner"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { MediaDeleteSelectionDialog } from "@/components/media/media-delete-selection-dialog"
import { en } from "@/i18n/en"
import { deleteSelectionExecute, getDeleteFootprint, getMediaWatch, getTrashSettings } from "@/lib/api"
import { footerCloseButton } from "@/test/dialog"
import { renderWithProviders } from "@/test/render"
import type { MediaDetail, MediaFileRead, TorrentRead } from "@/types/media"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  deleteSelectionExecute: vi.fn(),
  getDeleteFootprint: vi.fn(),
  getMediaWatch: vi.fn(),
  getTrashSettings: vi.fn(),
}))

function torrent(id: number, name: string, size: number): TorrentRead {
  return {
    id,
    hash: `hash-${id}`,
    name,
    save_path: "/data/torrents/movies",
    content_path: `/data/torrents/movies/${name}.mkv`,
    size,
    is_cross_seed: false,
    is_hardlinked: true,
    matched_by_name: false,
    repairable: false,
    not_imported: false,
    ratio: 1.5,
    seeders: 3,
    leechers: 0,
    added_on: null,
    completed_on: null,
    trackers: [],
  }
}

function file(id: number, name: string, size: number, isCurrent: boolean): MediaFileRead {
  return { id, path: `/data/media/movies/Inception (2010)/${name}`, size, episode_label: null, is_current: isCurrent }
}

const MEDIA: MediaDetail = {
  id: 7,
  media_type: "movie",
  title: "Inception",
  year: 2010,
  statuses: ["doublon"],
  reclaimable_bytes: 2000,
  total_size: 3000,
  has_poster: false,
  poster_image_tag: null,
  last_scanned_at: "2026-09-23T10:00:00",
  has_emby_item: true,
  date_added: null,
  watch_user_count: 0,
  watch_played_count: 0,
  watch_in_progress_count: 0,
  last_played_at: null,
  requested_by: null,
  arr_instance_name: null,
  radarr_id: 5,
  sonarr_id: null,
  emby_item_id: "e-inception",
  tmdb_id: 27205,
  tvdb_id: null,
  imdb_id: null,
  files: [file(10, "Inception.2160p.mkv", 3000, true), file(11, "Inception.1080p.mkv", 2000, false)],
  torrents: [torrent(1, "Inception.2010.2160p", 3000), torrent(2, "Inception.2010.1080p", 2000)],
  missing_emby_episodes: [],
  requests: [],
  import_issues: [],
}

async function openDialog(onMediaDeleted = vi.fn()) {
  const user = userEvent.setup()
  renderWithProviders(<MediaDeleteSelectionDialog media={MEDIA} onMediaDeleted={onMediaDeleted} />)
  await user.click(screen.getByRole("button", { name: en.deleteSelection.trigger }))
  await screen.findByText(en.deleteSelection.title)
  return user
}

const getConfirm = () => screen.getByRole("button", { name: en.common.confirmDelete })
const counter = (selected: number) =>
  en.deleteSelection.selectedCount.replace("{selected}", String(selected)).replace("{total}", "4")

/** Coche tous les torrents (groupe), puis le fichier 1080p de la bibliothèque. */
async function selectTorrentsAndOldFile(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("checkbox", { name: en.deleteSelection.torrents }))
  const expandLibrary = en.deleteSelection.expand.replace("{name}", en.deleteSelection.library)
  await user.click(screen.getByRole("button", { name: expandLibrary }))
  await user.click(screen.getByRole("checkbox", { name: "Inception.1080p.mkv" }))
}

describe("MediaDeleteSelectionDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getDeleteFootprint).mockResolvedValue({ units: [], torrents: [], files: [] })
    vi.mocked(getMediaWatch).mockResolvedValue({
      available: false,
      live: false,
      total_episodes: null,
      users: [],
      played_count: 0,
      in_progress_count: 0,
      last_played_at: null,
      last_played_by: null,
      date_added: null,
    })
    vi.mocked(getTrashSettings).mockResolvedValue({ enabled: false, retention_days: 7, min_days: 1, max_days: 90 })
    vi.mocked(deleteSelectionExecute).mockResolvedValue({ steps: [], media_deleted: false })
  })

  it("keeps confirmation disabled until something is checked", async () => {
    await openDialog()

    expect(screen.getByText(counter(0))).toBeInTheDocument()
    expect(getConfirm()).toBeDisabled()
  })

  it("counts the checked items as the selection grows", async () => {
    const user = await openDialog()

    await selectTorrentsAndOldFile(user)

    expect(screen.getByText(counter(3))).toBeInTheDocument()
    expect(getConfirm()).toBeEnabled()
  })

  it("never calls the API when the dialog is closed", async () => {
    const user = await openDialog()
    await selectTorrentsAndOldFile(user)

    await user.click(footerCloseButton())

    await waitFor(() => expect(screen.queryByText(en.deleteSelection.title)).not.toBeInTheDocument())
    expect(deleteSelectionExecute).not.toHaveBeenCalled()
  })

  it("sends exactly the checked torrents and files", async () => {
    const user = await openDialog()
    await selectTorrentsAndOldFile(user)

    await user.click(getConfirm())

    await waitFor(() => expect(deleteSelectionExecute).toHaveBeenCalledTimes(1))
    // Une partie de la bibliothèque seulement : le film reste dans Radarr.
    expect(deleteSelectionExecute).toHaveBeenCalledWith(MEDIA.id, {
      torrent_ids: [1, 2],
      media_file_ids: [11],
      remove_from_arr: false,
    })
  })

  it("removes the movie from Radarr only when everything is deleted", async () => {
    const user = await openDialog()

    await user.click(screen.getByRole("button", { name: en.deleteSelection.selectAll }))
    expect(screen.getByRole("checkbox", { name: en.deleteSelection.removeMovie })).toBeChecked()
    await user.click(getConfirm())

    await waitFor(() => expect(deleteSelectionExecute).toHaveBeenCalledTimes(1))
    expect(deleteSelectionExecute).toHaveBeenCalledWith(MEDIA.id, {
      torrent_ids: [1, 2],
      media_file_ids: [10, 11],
      remove_from_arr: true,
    })
  })

  it("leaves the page when nothing remains of the media", async () => {
    vi.mocked(deleteSelectionExecute).mockResolvedValue({ steps: [], media_deleted: true })
    const onMediaDeleted = vi.fn()
    const user = await openDialog(onMediaDeleted)
    await user.click(screen.getByRole("button", { name: en.deleteSelection.selectAll }))

    await user.click(getConfirm())

    await waitFor(() => expect(onMediaDeleted).toHaveBeenCalledTimes(1))
  })

  it("says the deletion was cancelled when a step failed", async () => {
    vi.mocked(deleteSelectionExecute).mockResolvedValue({
      steps: [
        { kind: "library_file", label: "Inception.1080p.mkv", success: false, error: "Permission denied" },
        { kind: "rollback", label: "Suppression annulée : tout a été remis en place.", success: false, error: null },
      ],
      media_deleted: false,
    })
    const user = await openDialog()
    await selectTorrentsAndOldFile(user)

    await user.click(getConfirm())

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(en.deleteSelection.cancelled))
    expect(toast.success).not.toHaveBeenCalled()
    expect(screen.getByText("Permission denied")).toBeInTheDocument()
  })

  it("shows the API error and keeps the dialog open", async () => {
    vi.mocked(deleteSelectionExecute).mockRejectedValue(new Error("Radarr injoignable"))
    const user = await openDialog()
    await selectTorrentsAndOldFile(user)

    await user.click(getConfirm())

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Radarr injoignable"))
    expect(screen.getByText(en.deleteSelection.title)).toBeInTheDocument()
    expect(screen.getByText(counter(3))).toBeInTheDocument() // la sélection est conservée
  })
})
