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
  torrents: [{ id: 1, units: [0] }],
  files: [
    { id: 10, units: [0] },
    { id: 11, units: [1] },
  ],
}
const index = indexFootprint(footprint)

describe("disk footprint", () => {
  it("counts a hardlinked file only once", () => {
    expect(diskBytes(footprint, index, [torrentKey(1), fileKey(10)])).toBe(1000)
  })

  it("frees nothing while one link is kept", () => {
    expect(reclaimedBytes(footprint, index, [torrentKey(1)])).toBe(0)
    expect(reclaimedBytes(footprint, index, [fileKey(10)])).toBe(0)
  })

  it("frees the space once every link is selected", () => {
    expect(reclaimedBytes(footprint, index, [torrentKey(1), fileKey(10)])).toBe(1000)
    expect(reclaimedBytes(footprint, index, [torrentKey(1), fileKey(10), fileKey(11)])).toBe(1500)
  })

  it("ignores unknown selection keys", () => {
    expect(reclaimedBytes(footprint, index, [fileKey(999)])).toBe(0)
  })
})
