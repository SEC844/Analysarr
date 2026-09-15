import { describe, expect, it } from "vitest"

import { isLocalNetworkHost } from "./clipboard"

describe("isLocalNetworkHost", () => {
  it("accepts local network hosts", () => {
    for (const host of [
      "localhost",
      "127.0.0.1",
      "10.0.20.110",
      "192.168.1.20",
      "172.16.0.5",
      "172.31.255.1",
      "169.254.10.1",
      "100.101.102.103",
      "tower.local",
      "nas.lan",
      "[::1]",
      "fd12:3456::1",
      "fe80::1",
    ]) {
      expect(isLocalNetworkHost(host), host).toBe(true)
    }
  })

  it("rejects public hosts", () => {
    for (const host of [
      "analysarr.example.com",
      "8.8.8.8",
      "172.32.0.1",
      "192.169.1.1",
      "100.128.0.1",
      "10.0.0.1.example.com",
      "local.example.com",
      "2001:db8::1",
    ]) {
      expect(isLocalNetworkHost(host), host).toBe(false)
    }
  })
})
