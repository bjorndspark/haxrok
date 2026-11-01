import {
  API_V1_PREFIX,
  apiDelete,
  apiGet,
  apiPost,
  apiUrl,
  type ErrorResponse,
} from "@/shared/api/client"

import type { Conversation, Message } from "./types"

export async function listConversations(): Promise<Conversation[]> {
  return apiGet<Conversation[]>("/chat/conversations")
}

export async function createConversation(title = ""): Promise<Conversation> {
  return apiPost<Conversation>("/chat/conversations", { title })
}

export async function deleteConversation(id: number): Promise<void> {
  await apiDelete(`/chat/conversations/${id}`)
}

export async function listMessages(conversationId: number): Promise<Message[]> {
  return apiGet<Message[]>(`/chat/conversations/${conversationId}/messages`)
}

export async function transcribeAudio(blob: Blob, language?: string): Promise<string> {
  const form = new FormData()
  form.append("file", blob, "recording.webm")
  if (language) form.append("language", language)
  const data = await apiPost<{ text: string }>("/speech/transcribe", form)
  return data.text
}

/** Returns an absolute URL the client can hand to an <audio> element. */
export async function synthesizeSpeech(text: string): Promise<string> {
  const data = await apiPost<{ url: string }>("/speech/synthesize", { text })
  return apiUrl(data.url)
}

export type ChatStreamEvent =
  | { type: "token"; token: string }
  | { type: "message"; message: Message }
  | { type: "error"; message: string }

/**
 * POST a user message and yield the assistant reply as it streams.
 * EventSource only supports GET, so SSE frames are parsed off a fetch stream.
 */
export async function* streamMessage(
  conversationId: number,
  content: string,
  generateImage: boolean,
): AsyncGenerator<ChatStreamEvent> {
  const res = await fetch(
    apiUrl(`${API_V1_PREFIX}/chat/conversations/${conversationId}/messages`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, generate_image: generateImage }),
    },
  )
  if (!res.ok || !res.body) {
    // Non-2xx responses carry the error envelope — surface its message.
    let detail = `HTTP ${res.status}`
    try {
      const body = (await res.json()) as ErrorResponse
      if (body.error?.message) detail = body.error.message
    } catch {
      // Body wasn't the envelope (proxy error, etc.) — keep the status.
    }
    throw new Error(`send failed: ${detail}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE frames are CRLF-separated (sse-starlette); normalize before splitting.
    buffer = buffer.replace(/\r\n/g, "\n")
    const frames = buffer.split("\n\n")
    buffer = frames.pop() ?? ""
    for (const frame of frames) {
      let event = "message"
      let data = ""
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim()
        else if (line.startsWith("data:")) data += line.slice(5).trim()
      }
      if (!data) continue
      if (event === "token")
        yield { type: "token", token: (JSON.parse(data) as { token: string }).token }
      else if (event === "message") yield { type: "message", message: JSON.parse(data) as Message }
      else if (event === "error")
        yield { type: "error", message: (JSON.parse(data) as { message: string }).message }
    }
  }
}
