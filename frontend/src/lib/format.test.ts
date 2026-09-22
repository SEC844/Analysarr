import { afterEach, describe, expect, it } from "vitest"

import { formatDateTime, parseApiDate, setDisplayTimeZone } from "./format"

afterEach(() => setDisplayTimeZone(""))

describe("parseApiDate", () => {
  it("lit une date sans fuseau comme de l'UTC", () => {
    // Convention de toutes les tables : les dates naïves sont en UTC. Sans ce
    // choix, le navigateur les lisait comme de l'heure locale et affichait
    // l'heure du conteneur (souvent décalée d'une ou deux heures).
    expect(parseApiDate("2026-09-22T06:06:00")?.toISOString()).toBe("2026-09-22T06:06:00.000Z")
  })

  it("respecte un fuseau déjà présent", () => {
    expect(parseApiDate("2026-09-22T08:06:00+02:00")?.toISOString()).toBe("2026-09-22T06:06:00.000Z")
    expect(parseApiDate("2026-09-22T06:06:00Z")?.toISOString()).toBe("2026-09-22T06:06:00.000Z")
  })

  it("renvoie null sur une valeur absente ou illisible", () => {
    expect(parseApiDate(null)).toBeNull()
    expect(parseApiDate("")).toBeNull()
    expect(parseApiDate("pas une date")).toBeNull()
  })
})

describe("formatDateTime", () => {
  it("affiche l'heure du fuseau choisi", () => {
    setDisplayTimeZone("Europe/Paris")
    expect(formatDateTime("2026-09-22T06:06:00")).toContain("08:06")
    setDisplayTimeZone("UTC")
    expect(formatDateTime("2026-09-22T06:06:00")).toContain("06:06")
  })

  it("retombe sur le fuseau du navigateur si celui choisi est inconnu", () => {
    setDisplayTimeZone("Pas/UnFuseau")
    expect(formatDateTime("2026-09-22T06:06:00")).not.toBeNull()
  })
})
