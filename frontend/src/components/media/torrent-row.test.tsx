import { screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { TorrentRow } from "@/components/media/torrent-row"
import { en } from "@/i18n/en"
import { renderWithProviders } from "@/test/render"
import type { TorrentRead } from "@/types/media"

function torrent(overrides: Partial<TorrentRead>): TorrentRead {
  return {
    id: 1,
    hash: "hash-1",
    name: "Dark.S02.1080p",
    save_path: "/data/torrents/series",
    content_path: "/data/torrents/series/Dark.S02.1080p",
    size: 1000,
    is_cross_seed: false,
    is_hardlinked: false,
    matched_by_name: false,
    repairable: false,
    not_imported: false,
    ignored: false,
    ignorable: false,
    ignore_rule_id: null,
    ratio: null,
    seeders: null,
    leechers: null,
    added_on: null,
    completed_on: null,
    trackers: [],
    ...overrides,
  }
}

function renderRow(overrides: Partial<TorrentRead>) {
  renderWithProviders(
    <ul>
      <TorrentRow mediaId={7} torrent={torrent(overrides)} />
    </ul>,
  )
}

describe("TorrentRow", () => {
  it("shows seasons downloaded ahead as not imported, never as orphans", () => {
    renderRow({ not_imported: true })

    expect(screen.getByText(en.media.notImported)).toHaveAttribute("title", en.media.notImportedHint)
    expect(screen.queryByText(en.media.orphan)).not.toBeInTheDocument()
  })

  it("keeps a real orphan an orphan", () => {
    renderRow({})

    expect(screen.getByText(en.media.orphan)).toBeInTheDocument()
    expect(screen.queryByText(en.media.notImported)).not.toBeInTheDocument()
  })

  it("shows a repairable copy as not hardlinked", () => {
    renderRow({ repairable: true })

    expect(screen.getByText(en.media.notHardlinked)).toBeInTheDocument()
    expect(screen.queryByText(en.media.orphan)).not.toBeInTheDocument()
  })
})
