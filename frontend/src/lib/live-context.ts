import { createContext } from "react"

/** Médias modifiés il y a quelques secondes (surbrillance), fournis par
 * `LiveEventsProvider`. */
export const RecentlyUpdatedContext = createContext<ReadonlySet<number>>(new Set())
