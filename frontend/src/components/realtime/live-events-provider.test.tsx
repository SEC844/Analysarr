import { act, render, screen } from "@testing-library/react"
import { QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { LiveEventsProvider } from "@/components/realtime/live-events-provider"
import { useRecentlyUpdated } from "@/hooks/use-recently-updated"
import { createTestQueryClient } from "@/test/render"

/** Faux EventSource : le test pilote l'ouverture, les messages et les coupures. */
class FakeEventSource {
  static instances: FakeEventSource[] = []
  onmessage: ((message: MessageEvent<string>) => void) | null = null
  onerror: (() => void) | null = null
  onopen: (() => void) | null = null
  closed = false
  readonly url: string

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  emit(event: object) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(event) }))
  }

  close() {
    this.closed = true
  }
}

function Probe({ id }: { id: number }) {
  return <span>{useRecentlyUpdated(id) ? `media ${id} highlighted` : `media ${id}`}</span>
}

function renderProvider() {
  const queryClient = createTestQueryClient()
  const invalidate = vi.spyOn(queryClient, "invalidateQueries")
  const remove = vi.spyOn(queryClient, "removeQueries")
  const view = render(
    <QueryClientProvider client={queryClient}>
      <LiveEventsProvider>
        <Probe id={7} />
      </LiveEventsProvider>
    </QueryClientProvider>,
  )
  const source = FakeEventSource.instances.at(-1)
  if (!source) throw new Error("aucun abonnement")
  return { view, source, invalidate, remove }
}

const keys = (spy: ReturnType<typeof vi.fn>) =>
  spy.mock.calls.map(([filters]) => JSON.stringify((filters as { queryKey: unknown }).queryKey))

describe("LiveEventsProvider", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.instances = []
    vi.stubGlobal("EventSource", FakeEventSource)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it("subscribes once to the authenticated event stream", () => {
    renderProvider()
    expect(FakeEventSource.instances.map((s) => s.url)).toEqual(["/api/events/stream"])
  })

  it("groups a burst, re-reads what it touches and highlights the media for a moment", () => {
    const { source, invalidate } = renderProvider()

    act(() => {
      source.emit({ type: "media.updated", media_id: 7 })
      source.emit({ type: "media.updated", media_id: 7 })
      source.emit({ type: "cleanup.changed" })
    })
    expect(invalidate).not.toHaveBeenCalled()

    act(() => vi.advanceTimersByTime(300))

    expect(keys(invalidate)).toEqual(
      expect.arrayContaining(['["media"]', '["media","detail",7]', '["media","watch",7]', '["cleanup"]']),
    )
    expect(keys(invalidate).filter((k) => k === '["media","detail",7]')).toHaveLength(1)
    expect(screen.getByText("media 7 highlighted")).toBeInTheDocument()

    act(() => vi.advanceTimersByTime(4100))
    expect(screen.getByText("media 7")).toBeInTheDocument()
  })

  it("forgets a removed media", () => {
    const { source, remove } = renderProvider()

    act(() => {
      source.emit({ type: "media.removed", media_id: 7 })
      vi.advanceTimersByTime(300)
    })

    expect(keys(remove)).toEqual(['["media","detail",7]'])
  })

  it("re-reads everything after reconnecting, since events may have been missed", () => {
    const { source, invalidate } = renderProvider()

    act(() => {
      source.onopen?.()
      vi.advanceTimersByTime(300)
    })
    expect(invalidate).not.toHaveBeenCalled()

    act(() => {
      source.onerror?.()
      source.onopen?.()
      vi.advanceTimersByTime(300)
    })
    expect(keys(invalidate)).toEqual(expect.arrayContaining(['["media"]', '["cleanup"]', '["realtime"]']))
  })

  it("closes the stream when the application unmounts", () => {
    const { view, source } = renderProvider()
    view.unmount()
    expect(source.closed).toBe(true)
  })
})
