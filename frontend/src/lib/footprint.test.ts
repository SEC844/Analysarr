import { describe, expect, it } from "vitest"

import { diskBytes, fileKey, indexFootprint, reclaimedBytes, torrentKey } from "@/lib/footprint"
import type { MediaDeleteFootprint } from "@/types/media"

// Un épisode (1000 o) présent en bibliothèque ET dans un torrent (même inode,
// 2 liens), plus un second épisode (500 o) uniquement en bibliothèque.
const footprint: MediaDeleteFootprint = {
  units: [
    { size: 1000, links: 2 },
    { size: 500, links: 1 },
  ],
  torrents: [{ id: 1, key: "abc", units: [0] }],
  files: [
    { id: 10, key: "/media/e1.mkv", units: [0] },
    { id: 11, key: "/media/e2.mkv", units: [1] },
  ],
}
const index = indexFootprint(footprint)

describe("disk footprint", () => {
  it("counts a hardlinked file only once", () => {
    expect(diskBytes(footprint, index, [torrentKey("ABC"), fileKey("/media/e1.mkv")])).toBe(1000)
  })

  it("frees nothing while one link is kept", () => {
    expect(reclaimedBytes(footprint, index, [torrentKey("ABC")])).toBe(0)
    expect(reclaimedBytes(footprint, index, [fileKey("/media/e1.mkv")])).toBe(0)
  })

  it("frees the space once every link is selected", () => {
    expect(reclaimedBytes(footprint, index, [torrentKey("ABC"), fileKey("/media/e1.mkv")])).toBe(1000)
    expect(reclaimedBytes(footprint, index, [torrentKey("ABC"), fileKey("/media/e1.mkv"), fileKey("/media/e2.mkv")])).toBe(1500)
  })

  it("ignores unknown selection keys", () => {
    expect(reclaimedBytes(footprint, index, [fileKey("/media/absent.mkv")])).toBe(0)
  })
})
