import { create } from "zustand"

/**
 * UI-only chat state — server data (conversations, messages) lives in
 * TanStack Query, never here.
 */
interface ChatUiState {
  activeConversationId: number | null
  sending: boolean
  /** Partial assistant text accumulating during a stream. */
  draft: string
  recording: boolean
  /** Composer toggle: next send is an image-generation request. */
  imageMode: boolean
  /** Voice STT language: "auto" or an ISO code ("en", "ja", …). */
  voiceLanguage: string
  /** Whisper's detected language in auto mode — pinned for the session. */
  detectedLanguage: string | null
  /** Opt-in full duplex: the VAD keeps listening while a reply plays —
   *  sustained speech barges in and cancels it. */
  talkOver: boolean
  /** User clicked "New chat" — suppress auto-select until they pick or send. */
  pendingNewChat: boolean
  error: string | null
  setActiveConversation: (id: number | null) => void
  newChat: () => void
  setSending: (v: boolean) => void
  appendDraft: (token: string) => void
  clearDraft: () => void
  setRecording: (v: boolean) => void
  setImageMode: (v: boolean) => void
  setVoiceLanguage: (v: string) => void
  setDetectedLanguage: (v: string | null) => void
  setTalkOver: (v: boolean) => void
  setError: (message: string | null) => void
}

export const useChatUi = create<ChatUiState>((set) => ({
  activeConversationId: null,
  sending: false,
  draft: "",
  recording: false,
  imageMode: false,
  voiceLanguage: "auto",
  detectedLanguage: null,
  talkOver: false,
  pendingNewChat: false,
  error: null,
  setActiveConversation: (id) =>
    set({ activeConversationId: id, pendingNewChat: false, error: null }),
  newChat: () => set({ activeConversationId: null, pendingNewChat: true }),
  setSending: (v) => set({ sending: v }),
  appendDraft: (token) => set((s) => ({ draft: s.draft + token })),
  clearDraft: () => set({ draft: "" }),
  setRecording: (v) => set({ recording: v }),
  setImageMode: (v) => set({ imageMode: v }),
  // A new selection drops the pinned detection — "auto" re-detects.
  setVoiceLanguage: (v) => set({ voiceLanguage: v, detectedLanguage: null }),
  setDetectedLanguage: (v) => set({ detectedLanguage: v }),
  setTalkOver: (v) => set({ talkOver: v }),
  setError: (message) => set({ error: message }),
}))
