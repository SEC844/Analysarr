import { afterEach, describe, expect, it, vi } from "vitest"

import { ApiError, getAppInfo, login, setSessionExpiredHandler } from "@/lib/api"

/** Un 401 sur un appel protégé ne doit JAMAIS recharger la page : la page
 * rechargée relançait les mêmes appels, reprenait un 401 et recommençait —
 * écran clignotant plusieurs fois par seconde, inutilisable (bug réel, vu
 * derrière un reverse-proxy où aucun cookie de session n'existait encore). */
function respond(status: number, body: unknown = { detail: "Non authentifié." }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    text: async () => JSON.stringify(body),
    json: async () => body,
  })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
  setSessionExpiredHandler(() => {})
})

describe("request", () => {
  it("prévient d'une session perdue sur un appel protégé", async () => {
    respond(401)
    const expired = vi.fn()
    setSessionExpiredHandler(expired)

    await expect(getAppInfo()).rejects.toBeInstanceOf(ApiError)

    expect(expired).toHaveBeenCalledTimes(1)
  })

  it("laisse les appels d'authentification gérer leur propre 401", async () => {
    respond(401, { detail: "Identifiants invalides." })
    const expired = vi.fn()
    setSessionExpiredHandler(expired)

    await expect(login({ username: "a", password: "b" })).rejects.toBeInstanceOf(ApiError)

    expect(expired).not.toHaveBeenCalled()
  })

  it("remonte le message lisible renvoyé par l'API", async () => {
    respond(409, { detail: "Volume non monté." })

    await expect(getAppInfo()).rejects.toThrow("Volume non monté.")
  })

  it("ne sert jamais une réponse d'API depuis un cache", async () => {
    const fetchMock = respond(200, { version: "0.25.0" })

    await getAppInfo()

    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ cache: "no-store" })
  })
})
