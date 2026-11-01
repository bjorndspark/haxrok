/**
 * Chat behavior behind the components: server data (TanStack Query), the
 * send/remove/read-aloud actions, and voice mode wired into the chat.
 * Components only render and call these.
 */
import { skipToken, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useRef } from "react"

import { errorMessage } from "@/shared/lib/utils"

import {
  createConversation,
  deleteConversation,
  listConversations,
  listMessages,
  streamMessage,
  synthesizeSpeech,
} from "./api"
import { useChatUi } from "./store"
import type { Conversation, Message } from "./types"
import { useVoice, type UseVoiceResult } from "./useVoice"

const keys = {
  conversations: ["conversations"] as const,
  messages: (id: number | null) => ["messages", id] as const,
}

/** Show the user's message immediately; the turn's refetch replaces it
 *  with the stored row. */
function addOptimisticUserMessage(qc: QueryClient, convId: number, content: string): void {
  qc.setQueryData<Message[]>(keys.messages(convId), (old = []) => [
    ...old,
    {
      id: -Date.now(),
      role: "user",
      content,
      media_url: null,
      created_at: new Date().toISOString(),
    },
  ])
}

function refetchConversation(qc: QueryClient, convId: number): void {
  void qc.invalidateQueries({ queryKey: keys.messages(convId) })
  void qc.invalidateQueries({ queryKey: keys.conversations })
}

export interface ChatData {
  conversations: Conversation[]
  messages: Message[]
  activeConversationId: number | null
}

/** The conversation list and the active conversation's messages. Selects
 *  the newest conversation once the list loads — unless the user asked
 *  for a blank chat (pendingNewChat), which must win. */
export function useChatData(): ChatData {
  const activeConversationId = useChatUi((s) => s.activeConversationId)
  const pendingNewChat = useChatUi((s) => s.pendingNewChat)
  const setActiveConversation = useChatUi((s) => s.setActiveConversation)

  const conversations = useQuery({ queryKey: keys.conversations, queryFn: listConversations })
  const messages = useQuery({
    queryKey: keys.messages(activeConversationId),
    queryFn: activeConversationId == null ? skipToken : () => listMessages(activeConversationId),
  })

  useEffect(() => {
    const convs = conversations.data
    if (activeConversationId == null && !pendingNewChat && convs?.length) {
      setActiveConversation(convs[0].id)
    }
  }, [conversations.data, activeConversationId, pendingNewChat, setActiveConversation])

  return {
    conversations: conversations.data ?? [],
    messages: messages.data ?? [],
    activeConversationId,
  }
}

/** Resolve the active conversation, creating (and selecting) one when the
 *  user is on a blank chat. */
function useEnsureConversation(): () => Promise<number> {
  const qc = useQueryClient()
  return useCallback(async () => {
    const active = useChatUi.getState().activeConversationId
    if (active != null) return active
    const conv = await createConversation()
    useChatUi.getState().setActiveConversation(conv.id)
    void qc.invalidateQueries({ queryKey: keys.conversations })
    return conv.id
  }, [qc])
}

/** Send a text (or image-mode) message and stream the reply into the
 *  draft. Failures — creating the conversation included — land in the
 *  error banner. */
export function useSendMessage(): (content: string, generateImage: boolean) => Promise<void> {
  const qc = useQueryClient()
  const ensureConversation = useEnsureConversation()
  return useCallback(
    async (content: string, generateImage: boolean) => {
      const ui = useChatUi.getState()
      ui.setSending(true)
      ui.setError(null)
      let convId: number | null = null
      try {
        convId = await ensureConversation()
        addOptimisticUserMessage(qc, convId, content)
        for await (const event of streamMessage(convId, content, generateImage)) {
          if (event.type === "token") ui.appendDraft(event.token)
          else if (event.type === "error") ui.setError(event.message)
        }
      } catch (e) {
        ui.setError(errorMessage(e, "send failed"))
      } finally {
        ui.setSending(false)
        ui.clearDraft()
        if (convId != null) refetchConversation(qc, convId)
      }
    },
    [qc, ensureConversation],
  )
}

/** Delete a conversation; leaves the blank chat if it was the active one. */
export function useRemoveConversation(): (id: number) => Promise<void> {
  const qc = useQueryClient()
  return useCallback(
    async (id: number) => {
      try {
        await deleteConversation(id)
        const ui = useChatUi.getState()
        if (ui.activeConversationId === id) ui.setActiveConversation(null)
      } catch (e) {
        useChatUi.getState().setError(errorMessage(e, "delete failed"))
      } finally {
        void qc.invalidateQueries({ queryKey: keys.conversations })
      }
    },
    [qc],
  )
}

/** Speak an assistant message through the synthesis endpoint. */
export function useReadAloud(): (text: string) => Promise<void> {
  return useCallback(async (text: string) => {
    try {
      const url = await synthesizeSpeech(text)
      await new Audio(url).play()
    } catch (e) {
      useChatUi.getState().setError(errorMessage(e, "read-aloud failed"))
    }
  }, [])
}

/** Hands-free voice mode bound into the chat: transcripts become
 *  optimistic bubbles, reply tokens stream into the draft, finished or
 *  interrupted turns refetch what the server stored. */
export function useChatVoice(): UseVoiceResult {
  const qc = useQueryClient()
  const ensureConversation = useEnsureConversation()
  // The conversation the live voice socket is bound to — set when it
  // connects; the active one may change independently.
  const boundRef = useRef<number | null>(null)

  return useVoice({
    ensureConversation: async () => {
      boundRef.current = await ensureConversation()
      return boundRef.current
    },
    onTranscript: (transcript) => {
      if (boundRef.current != null) addOptimisticUserMessage(qc, boundRef.current, transcript)
    },
    onToken: (token) => {
      useChatUi.getState().appendDraft(token)
    },
    onTurnComplete: () => {
      useChatUi.getState().clearDraft()
      if (boundRef.current != null) refetchConversation(qc, boundRef.current)
    },
    onError: (message) => {
      useChatUi.getState().setError(message)
    },
  })
}
