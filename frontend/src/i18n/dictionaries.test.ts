import { describe, expect, it } from "vitest"

import { en } from "@/i18n/en"
import { fr } from "@/i18n/fr"

type Tree = { [key: string]: string | Tree }

function flatten(tree: Tree, prefix = ""): Map<string, string> {
  const entries = new Map<string, string>()
  for (const [key, value] of Object.entries(tree)) {
    const path = `${prefix}${key}`
    if (typeof value === "string") entries.set(path, value)
    else for (const [sub, text] of flatten(value, `${path}.`)) entries.set(sub, text)
  }
  return entries
}

// {server} et {deServer} désignent tous deux le nom du serveur multimédia :
// le français a besoin de la forme élidée, pas forcément l'anglais.
function placeholders(text: string): string[] {
  const names = [...text.matchAll(/\{(\w+)\}/g)].flatMap((m) => (m[1] ? [m[1]] : []))
  return [...new Set(names.map((name) => (name === "deServer" ? "server" : name)))].sort()
}

const frEntries = flatten(fr as unknown as Tree)
const enEntries = flatten(en as unknown as Tree)

describe("translations", () => {
  it("have the same keys in French and English", () => {
    expect([...enEntries.keys()].sort()).toEqual([...frEntries.keys()].sort())
  })

  it("use the same variables in both languages", () => {
    for (const [key, text] of frEntries) {
      expect(placeholders(enEntries.get(key) ?? ""), key).toEqual(placeholders(text))
    }
  })

  it("never hardcode the media server name", () => {
    const hardcoded = [...frEntries, ...enEntries].filter(
      ([key, text]) => /\b(Emby|Jellyfin)\b/.test(text) && !key.startsWith("services.emby.") && !key.startsWith("services.jellyfin."),
    )
    expect(hardcoded.map(([key]) => key)).toEqual([])
  })
})
