import { act, renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  useDeleteExecuteMutation,
  useDeleteSelectionExecuteMutation,
  useHardlinkRepairExecuteMutation,
} from "@/hooks/use-media"
import { deleteExecute, deleteSelectionExecute, hardlinkRepairExecute } from "@/lib/api"
import { createTestQueryClient, createWrapper } from "@/test/render"

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  deleteExecute: vi.fn(),
  deleteSelectionExecute: vi.fn(),
  hardlinkRepairExecute: vi.fn(),
}))

const MEDIA_ID = 7

function setup<T>(useHook: () => T) {
  const queryClient = createTestQueryClient()
  const invalidate = vi.spyOn(queryClient, "invalidateQueries")
  const { result } = renderHook(useHook, { wrapper: createWrapper(queryClient) })
  return { result, invalidate }
}

// La bibliothèque ET la fiche doivent être relues : statuts, fichiers et
// espace récupérable viennent de changer côté serveur.
function expectMediaRefreshed(invalidate: ReturnType<typeof vi.spyOn>) {
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["media"] })
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["media", "detail", MEDIA_ID] })
}

describe("destructive media mutations", () => {
  beforeEach(() => vi.clearAllMocks())

  it("refreshes the library and the page after a cleanup", async () => {
    vi.mocked(deleteExecute).mockResolvedValue({ steps: [] })
    const { result, invalidate } = setup(useDeleteExecuteMutation)

    act(() => result.current.mutate(MEDIA_ID))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expectMediaRefreshed(invalidate)
  })

  it("refreshes the library and the page after a selective deletion", async () => {
    vi.mocked(deleteSelectionExecute).mockResolvedValue({ steps: [], media_deleted: false })
    const { result, invalidate } = setup(useDeleteSelectionExecuteMutation)

    act(() =>
      result.current.mutate({
        id: MEDIA_ID,
        selection: { torrent_ids: [1], media_file_ids: [], remove_from_arr: false },
      }),
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expectMediaRefreshed(invalidate)
  })

  it("refreshes the library and the page after a hardlink repair", async () => {
    vi.mocked(hardlinkRepairExecute).mockResolvedValue({ steps: [] })
    const { result, invalidate } = setup(useHardlinkRepairExecuteMutation)

    act(() => result.current.mutate(MEDIA_ID))

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expectMediaRefreshed(invalidate)
  })

  it("keeps the cache untouched when the deletion fails", async () => {
    vi.mocked(deleteExecute).mockRejectedValue(new Error("Montage absent"))
    const { result, invalidate } = setup(useDeleteExecuteMutation)

    act(() => result.current.mutate(MEDIA_ID))

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(invalidate).not.toHaveBeenCalled()
  })
})
