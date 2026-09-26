import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { FileOptions, MediaAlertOptions, TorrentOptions } from "@/components/media/ignore-actions"
import { StatusBadgeList } from "@/components/media/status-badge"
import { en } from "@/i18n/en"
import { createIgnore, deleteIgnore } from "@/lib/api"
import { renderWithProviders } from "@/test/render"
import type { MediaDetail, MediaFileRead, TorrentRead } from "@/types/media"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  createIgnore: vi.fn(),
  deleteIgnore: vi.fn(),
}))

const TORRENT: TorrentRead = {
  id: 3,
  hash: "abc",
  name: "Matrix.1999.720p",
  save_path: "/data/torrents",
  content_path: "/data/torrents/Matrix.1999.720p.mkv",
  size: 100,
  is_cross_seed: false,
  is_hardlinked: false,
  matched_by_name: false,
  repairable: false,
  not_imported: false,
  ignored: false,
  ignorable: true,
  ignore_rule_id: null,
  ratio: null,
  seeders: null,
  leechers: null,
  added_on: null,
  completed_on: null,
  trackers: [],
}

const FILE: MediaFileRead = {
  id: 8,
  path: "/data/media/Matrix.VOSTFR.mkv",
  size: 100,
  episode_label: null,
  is_current: false,
  ignored: false,
  ignorable: true,
  ignore_rule_id: null,
}

const MEDIA: MediaDetail = {
  id: 7,
  media_type: "movie",
  title: "Matrix",
  year: 1999,
  statuses: ["manquant_arr", "manquant_qbit", "cross_seed"],
  muted_statuses: ["doublon"],
  reclaimable_bytes: 0,
  total_size: 0,
  has_poster: false,
  poster_image_tag: null,
  last_scanned_at: "2026-09-26T10:00:00",
  has_emby_item: true,
  date_added: null,
  watch_user_count: 0,
  watch_played_count: 0,
  watch_in_progress_count: 0,
  last_played_at: null,
  requested_by: null,
  arr_instance_name: null,
  radarr_id: null,
  sonarr_id: null,
  emby_item_id: "e",
  tmdb_id: null,
  tvdb_id: null,
  imdb_id: null,
  files: [],
  torrents: [],
  missing_emby_episodes: [],
  requests: [],
  import_issues: [],
  muted_rules: [{ status: "doublon", rule_id: 41, note: null }],
}

const optionsButton = () => screen.getByRole("button", { name: en.ignore.options })
const confirmButton = () => screen.getByRole("button", { name: en.ignore.confirm })

describe("ignore actions", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(createIgnore).mockResolvedValue(undefined)
    vi.mocked(deleteIgnore).mockResolvedValue(undefined)
  })

  it("ignores a torrent only after confirmation, with its note", async () => {
    const user = userEvent.setup()
    renderWithProviders(<TorrentOptions mediaId={7} torrent={TORRENT} />)

    await user.click(optionsButton())
    await user.click(await screen.findByRole("menuitem", { name: en.ignore.ignoreTorrent }))
    expect(screen.getByText(en.ignore.ignoreTorrentDescription)).toBeInTheDocument()
    expect(createIgnore).not.toHaveBeenCalled()
    await user.type(screen.getByLabelText(en.ignore.note), "  Seed longue durée  ")
    await user.click(confirmButton())

    await waitFor(() =>
      expect(createIgnore).toHaveBeenCalledWith({ media_id: 7, kind: "torrent", torrent_id: 3, note: "Seed longue durée" }),
    )
  })

  it("never ignores anything when the dialog is cancelled", async () => {
    const user = userEvent.setup()
    renderWithProviders(<TorrentOptions mediaId={7} torrent={TORRENT} />)

    await user.click(optionsButton())
    await user.click(await screen.findByRole("menuitem", { name: en.ignore.ignoreTorrent }))
    await user.click(screen.getByRole("button", { name: en.common.cancel }))

    await waitFor(() => expect(screen.queryByText(en.ignore.ignoreTorrentDescription)).not.toBeInTheDocument())
    expect(createIgnore).not.toHaveBeenCalled()
  })

  it("stops ignoring a torrent through its rule", async () => {
    const user = userEvent.setup()
    renderWithProviders(<TorrentOptions mediaId={7} torrent={{ ...TORRENT, ignored: true, ignorable: false, ignore_rule_id: 12 }} />)

    await user.click(optionsButton())
    await user.click(await screen.findByRole("menuitem", { name: en.ignore.unignore }))

    await waitFor(() => expect(deleteIgnore).toHaveBeenCalledWith(12))
  })

  it("offers nothing for a torrent that raises no alert", () => {
    renderWithProviders(<TorrentOptions mediaId={7} torrent={{ ...TORRENT, is_hardlinked: true, ignorable: false }} />)

    expect(screen.queryByRole("button", { name: en.ignore.options })).not.toBeInTheDocument()
  })

  it("keeps a duplicate file on purpose", async () => {
    const user = userEvent.setup()
    renderWithProviders(<FileOptions mediaId={7} file={FILE} />)

    await user.click(optionsButton())
    await user.click(await screen.findByRole("menuitem", { name: en.ignore.keepFile }))
    await user.click(confirmButton())

    await waitFor(() => expect(createIgnore).toHaveBeenCalledWith({ media_id: 7, kind: "file", file_id: 8, note: undefined }))
  })

  it("hides every alert of a media at once, never an information", async () => {
    const user = userEvent.setup()
    renderWithProviders(<MediaAlertOptions media={MEDIA} />)

    await user.click(optionsButton())
    expect(screen.queryByRole("menuitem", { name: /Cross-seed/ })).not.toBeInTheDocument()
    await user.click(await screen.findByRole("menuitem", { name: en.ignore.muteAll }))
    await user.click(confirmButton())

    await waitFor(() =>
      expect(createIgnore).toHaveBeenCalledWith({
        media_id: 7,
        kind: "status",
        statuses: ["manquant_arr", "manquant_qbit"],
        note: undefined,
      }),
    )
  })

  it("shows a hidden alert again", async () => {
    const user = userEvent.setup()
    renderWithProviders(<MediaAlertOptions media={MEDIA} />)

    await user.click(optionsButton())
    await user.click(
      await screen.findByRole("menuitem", { name: en.ignore.unmute.replace("{status}", en.status.doublon) }),
    )

    await waitFor(() => expect(deleteIgnore).toHaveBeenCalledWith(41))
  })
})

describe("StatusBadgeList", () => {
  it("keeps hidden alerts visible, struck through, next to Healthy", () => {
    renderWithProviders(<StatusBadgeList statuses={[]} muted={["manquant_arr"]} />)

    expect(screen.getByText(en.status.sain)).toBeInTheDocument()
    const muted = screen.getByText(en.status.manquant_arr).closest("[title]")
    expect(muted).toHaveAttribute("title", en.ignore.mutedHint)
    expect(muted).toHaveClass("line-through")
  })
})
