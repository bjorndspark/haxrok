// @vitest-environment node — exercises fetch/Response/ReadableStream only;
// skips the jsdom startup (~0.7s) that component tests still get by default.
import { afterEach, describe, expect, it, vi } from "vitest"

import { streamMessage } from "./api"

function sseResponse(frames: string): Response {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(frames))
      controller.close()
    },
  })
  return new Response(stream, { status: 200 })
}

async function drain<T>(stream: AsyncIterable<T>): Promise<T[]> {
  const events: T[] = []
  for await (const e of stream) events.push(e)
  return events
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("streamMessage", () => {
  it("parses CRLF-separated SSE frames", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          sseResponse(
            'event: token\r\ndata: {"token":"Hi"}\r\n\r\n' +
              'event: token\r\ndata: {"token":" there"}\r\n\r\n' +
              'event: message\r\ndata: {"id":1,"role":"assistant","content":"Hi there","media_url":null,"created_at":"2026-01-01T00:00:00Z"}\r\n\r\n',
          ),
        ),
    )

    const events = []
    for await (const e of streamMessage(1, "hello", false)) events.push(e)

    expect(events).toEqual([
      { type: "token", token: "Hi" },
      { type: "token", token: " there" },
      {
        type: "message",
        message: {
          id: 1,
          role: "assistant",
          content: "Hi there",
          media_url: null,
          created_at: "2026-01-01T00:00:00Z",
        },
      },
    ])
  })

  it("surfaces stream error events", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          sseResponse('event: error\r\ndata: {"message":"upstream down"}\r\n\r\n'),
        ),
    )

    const events = []
    for await (const e of streamMessage(1, "hello", false)) events.push(e)

    expect(events).toEqual([{ type: "error", message: "upstream down" }])
  })

  it("throws on non-OK responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 404 })))

    await expect(async () => {
      await drain(streamMessage(1, "hello", false))
    }).rejects.toThrow("404")
  })

  it("surfaces the error envelope message on non-OK responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            success: false,
            error: { code: "NOT_FOUND", message: "conversation 1 not found" },
            request_id: "r-1",
          }),
          { status: 404, headers: { "Content-Type": "application/json" } },
        ),
      ),
    )

    await expect(async () => {
      await drain(streamMessage(1, "hello", false))
    }).rejects.toThrow("conversation 1 not found")
  })
})
