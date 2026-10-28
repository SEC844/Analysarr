import { screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { TrashSection } from "@/components/settings/trash-section"
import { en } from "@/i18n/en"
import { getTrash, getTrashSettings } from "@/lib/api"
import { renderWithProviders } from "@/test/render"

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getTrash: vi.fn(),
  getTrashSettings: vi.fn(),
}))

describe("TrashSection", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getTrashSettings).mockResolvedValue({ enabled: true, retention_days: 1, min_days: 1, max_days: 90 })
  })

  it("never claims the trash is empty while its content is still loading", async () => {
    // Bug réel : « La corbeille est vide » s'affichait le temps que la liste
    // (chaque fichier mesuré sur le disque) arrive, puis la suppression apparaissait.
    vi.mocked(getTrash).mockReturnValue(new Promise(() => {}))
    renderWithProviders(<TrashSection />)

    expect(await screen.findByText(en.trash.contentTitle)).toBeInTheDocument()
    expect(screen.queryByText(en.trash.empty0)).not.toBeInTheDocument()
  })

  it("says the trash is empty once the list is known", async () => {
    vi.mocked(getTrash).mockResolvedValue([])
    renderWithProviders(<TrashSection />)

    expect(await screen.findByText(en.trash.empty0)).toBeInTheDocument()
  })
})
