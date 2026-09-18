import { describe, expect, it } from "vitest"

import {
  emptySettingsWrite,
  isCoreConfigComplete,
  missingCoreConfig,
  torrentCredentialsRequired,
  type SettingsRead,
  type SettingsWrite,
} from "./settings"

function savedNothing(): SettingsRead {
  return {
    configured: false,
    arr_instances: [],
    media_server: "emby",
    watch: { excluded_emby_user_ids: [] },
    emby: { url: null, api_key_set: false },
    sonarr: { url: null, api_key_set: false },
    radarr: { url: null, api_key_set: false },
    qbittorrent: { client: "qbittorrent", url: null, username: null, password_set: false },
    paths: { emby_library_path: null, qbittorrent_download_path: null },
    cross_seed: { enabled: false, url: null, api_key_set: false, library_path: null },
    seer: { enabled: false, url: null, api_key_set: false },
    schedule: { enabled: false, interval_minutes: null },
  }
}

function filledForm(): SettingsWrite {
  return {
    ...emptySettingsWrite(),
    emby_url: "http://emby:8096",
    emby_api_key: "k",
    sonarr_url: "http://sonarr:8989",
    sonarr_api_key: "k",
    radarr_url: "http://radarr:7878",
    radarr_api_key: "k",
    qbittorrent_url: "http://qbittorrent:8080",
    qbittorrent_username: "admin",
    qbittorrent_password: "pw",
    emby_library_path: "/data/media",
    qbittorrent_download_path: "/data/downloads",
  }
}

describe("torrentCredentialsRequired", () => {
  it("n'exige qu'un mot de passe pour Deluge", () => {
    expect(torrentCredentialsRequired("deluge")).toEqual({ username: false, password: true })
  })

  it("n'exige rien pour Transmission", () => {
    expect(torrentCredentialsRequired("transmission")).toEqual({ username: false, password: false })
  })
})

describe("missingCoreConfig", () => {
  it("ne signale rien quand tout est rempli", () => {
    expect(missingCoreConfig(filledForm(), savedNothing())).toEqual([])
  })

  it("accepte Transmission sans identifiant ni mot de passe", () => {
    const form: SettingsWrite = {
      ...filledForm(),
      torrent_client: "transmission",
      qbittorrent_username: "",
      qbittorrent_password: "",
    }
    expect(isCoreConfigComplete(form, savedNothing())).toBe(true)
  })

  it("exige le mot de passe de l'interface web de Deluge, jamais d'identifiant", () => {
    const form: SettingsWrite = {
      ...filledForm(),
      torrent_client: "deluge",
      qbittorrent_username: "",
      qbittorrent_password: "",
    }
    expect(missingCoreConfig(form, savedNothing())).toEqual(["torrentClient"])
    expect(isCoreConfigComplete({ ...form, qbittorrent_password: "pw" }, savedNothing())).toBe(true)
  })

  it("exige identifiant et mot de passe pour qBittorrent", () => {
    const form: SettingsWrite = { ...filledForm(), qbittorrent_username: "" }
    expect(missingCoreConfig(form, savedNothing())).toEqual(["torrentClient"])
  })

  it("accepte un secret déjà enregistré, jamais renvoyé au navigateur", () => {
    const saved = savedNothing()
    saved.emby.api_key_set = true
    saved.qbittorrent.password_set = true
    const form: SettingsWrite = { ...filledForm(), emby_api_key: "", qbittorrent_password: "" }
    expect(missingCoreConfig(form, saved)).toEqual([])
  })

  it("liste chaque étape obligatoire manquante, dans l'ordre de l'assistant", () => {
    expect(missingCoreConfig(emptySettingsWrite(), savedNothing())).toEqual([
      "mediaServer",
      "sonarr",
      "radarr",
      "torrentClient",
      "paths",
    ])
  })
})
