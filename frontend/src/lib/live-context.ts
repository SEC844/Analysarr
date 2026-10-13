import { createContext } from "react"

/** Médias modifiés il y a quelques secondes (surbrillance), tenus par
 * `LiveEventsProvider`. Un magasin plutôt qu'un ensemble passé par le
 * contexte : un changement ne réveille que les cartes concernées, pas toute
 * la grille. */
export class RecentlyUpdatedStore {
  private ids = new Set<number>()
  private listeners = new Set<() => void>()

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  has = (id: number): boolean => this.ids.has(id)

  add(ids: readonly number[]): void {
    for (const id of ids) this.ids.add(id)
    this.notify()
  }

  remove(ids: readonly number[]): void {
    for (const id of ids) this.ids.delete(id)
    this.notify()
  }

  private notify(): void {
    for (const listener of this.listeners) listener()
  }
}

export const RecentlyUpdatedContext = createContext(new RecentlyUpdatedStore())
