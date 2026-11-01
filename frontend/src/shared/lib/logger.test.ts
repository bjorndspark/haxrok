import { afterEach, describe, expect, it, vi } from "vitest"
import { flushRemoteLogs, initRemoteLogging } from "./logger"

describe("initRemoteLogging", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
  })

  it("ships console output to the backend log endpoint", () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }))
    vi.stubGlobal("fetch", fetchMock)
    vi.stubEnv("VITE_API_URL", "http://backend.test")

    initRemoteLogging()
    console.error("boom", { code: 42 })
    flushRemoteLogs()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe("http://backend.test/api/v1/logs/frontend")
    const body = JSON.parse(init.body as string) as {
      session_id: string
      entries: { level: string; source: string; message: string }[]
    }
    expect(body.session_id).toBeTruthy()
    expect(body.entries).toHaveLength(1)
    expect(body.entries[0].level).toBe("ERROR")
    expect(body.entries[0].source).toBe("console")
    expect(body.entries[0].message).toContain("boom")
    expect(body.entries[0].message).toContain('"code":42')
    // keepalive lets the same request outlive the page on pagehide.
    expect(init.keepalive).toBe(true)
  })

  it("does nothing without VITE_API_URL", () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }))
    vi.stubGlobal("fetch", fetchMock)
    vi.stubEnv("VITE_API_URL", "")

    initRemoteLogging()
    console.warn("ignored")
    flushRemoteLogs()

    expect(fetchMock).not.toHaveBeenCalled()
  })
})
