import { screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { MediaRequests } from "@/components/media/media-requests"
import { renderWithProviders } from "@/test/render"
import type { MediaRequestRead } from "@/types/media"

const MARIE = { name: "Marie", emby_user_id: null, image_tag: null }

function approved(overrides: Partial<MediaRequestRead>): MediaRequestRead {
  return {
    status: "approved",
    is_4k: false,
    seasons: [],
    requested_at: null,
    requested_by: MARIE,
    modified_by: null,
    auto_approved: false,
    ...overrides,
  }
}

describe("MediaRequests", () => {
  it("never claims an approval was automatic when the manager does not say so", () => {
    // Ombi : ni approbateur, ni approbation automatique connus.
    renderWithProviders(<MediaRequests requests={[approved({})]} />)

    expect(screen.getByText("approved")).toBeInTheDocument()
    expect(screen.queryByText(/auto-approved/)).not.toBeInTheDocument()
  })

  it("shows who approved, or that it was automatic, when Seer says so", () => {
    renderWithProviders(
      <MediaRequests
        requests={[approved({ auto_approved: true }), approved({ modified_by: { ...MARIE, name: "Admin" } })]}
      />,
    )

    expect(screen.getByText(/auto-approved/)).toBeInTheDocument()
    expect(screen.getByText(/approved by Admin/)).toBeInTheDocument()
  })
})
