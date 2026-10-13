import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { MemoryRouter, useNavigate } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"

import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { useIncrementalList } from "@/hooks/use-incremental-list"
import { useRememberedPath, useSessionState } from "@/hooks/use-session-state"

function router(url = "/") {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <MemoryRouter initialEntries={[url]}>{children}</MemoryRouter>
  }
}

/** Faux IntersectionObserver : le test décide quand la sentinelle devient visible. */
class FakeObserver {
  static instances: FakeObserver[] = []
  readonly callback: IntersectionObserverCallback
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
    FakeObserver.instances.push(this)
  }
  observe() {}
  disconnect() {}
  reveal() {
    this.callback([{ isIntersecting: true } as IntersectionObserverEntry], this as unknown as IntersectionObserver)
  }
}

describe("useSessionState", () => {
  it("finds its value again after the page was left", () => {
    const first = renderHook(() => useSessionState("probe", 1))
    act(() => first.result.current[1](5))
    first.unmount()

    const second = renderHook(() => useSessionState("probe", 1))
    expect(second.result.current[0]).toBe(5)
  })
})

describe("useRememberedPath", () => {
  it("reopens a page with the filters it was left with", () => {
    const { result } = renderHook(
      () => ({ remembered: useRememberedPath(), navigate: useNavigate() }),
      { wrapper: router("/cleanup?type=movie") },
    )
    expect(result.current.remembered("/cleanup")).toBe("/cleanup?type=movie")

    act(() => result.current.navigate("/media/3"))

    expect(result.current.remembered("/cleanup")).toBe("/cleanup?type=movie")
    expect(result.current.remembered("/")).toBe("/")
  })
})

describe("useDebouncedValue", () => {
  afterEach(() => vi.useRealTimers())

  it("waits for a pause before following the value", () => {
    vi.useFakeTimers()
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 250), {
      initialProps: { value: "a" },
    })
    rerender({ value: "ab" })
    rerender({ value: "abc" })
    expect(result.current).toBe("a")

    act(() => vi.advanceTimersByTime(250))
    expect(result.current).toBe("abc")
  })
})

describe("useIncrementalList", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    FakeObserver.instances = []
  })

  it("shows everything where the browser cannot tell what is visible", () => {
    const items = Array.from({ length: 500 }, (_, i) => i)
    const { result } = renderHook(() => useIncrementalList(items), { wrapper: router() })
    expect(result.current.visible).toHaveLength(500)
  })

  it("adds a slice when the end of the grid comes close", () => {
    vi.stubGlobal("IntersectionObserver", FakeObserver)
    const items = Array.from({ length: 500 }, (_, i) => i)
    const { result } = renderHook(() => useIncrementalList(items), { wrapper: router() })
    expect(result.current.visible).toHaveLength(120)

    act(() => result.current.sentinel(document.createElement("div")))
    act(() => FakeObserver.instances.at(-1)?.reveal())

    expect(result.current.visible).toHaveLength(240)
  })
})
