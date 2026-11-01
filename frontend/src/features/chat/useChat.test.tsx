import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import * as api from "./api"
import { useChatUi } from "./store"
import { useRemoveConversation, useSendMessage } from "./useChat"

vi.mock("./api")

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  useChatUi.setState({ activeConversationId: null, sending: false, draft: "", error: null })
})

afterEach(() => {
  vi.resetAllMocks()
})

describe("useSendMessage", () => {
  it("surfaces a failed conversation create instead of an unhandled rejection", async () => {
    vi.mocked(api.createConversation).mockRejectedValue(new Error("backend down"))
    const { result } = renderHook(() => useSendMessage(), { wrapper })

    await act(() => result.current("hi", false))

    const ui = useChatUi.getState()
    expect(ui.error).toBe("backend down")
    expect(ui.sending).toBe(false)
    expect(api.streamMessage).not.toHaveBeenCalled()
  })

  it("streams tokens into the draft and clears it when done", async () => {
    useChatUi.setState({ activeConversationId: 7 })
    const drafts: string[] = []
    vi.mocked(api.streamMessage).mockImplementation(async function* () {
      await Promise.resolve()
      yield { type: "token", token: "He" }
      drafts.push(useChatUi.getState().draft)
      yield { type: "token", token: "llo" }
      drafts.push(useChatUi.getState().draft)
    })
    const { result } = renderHook(() => useSendMessage(), { wrapper })

    await act(() => result.current("hi", false))

    expect(api.streamMessage).toHaveBeenCalledWith(7, "hi", false)
    expect(drafts).toEqual(["He", "Hello"])
    expect(useChatUi.getState().draft).toBe("")
  })
})

describe("useRemoveConversation", () => {
  it("surfaces a failed delete", async () => {
    vi.mocked(api.deleteConversation).mockRejectedValue(new Error("nope"))
    const { result } = renderHook(() => useRemoveConversation(), { wrapper })

    await act(() => result.current(3))

    expect(useChatUi.getState().error).toBe("nope")
  })
})
