export interface Conversation {
  id: number
  title: string
  created_at: string
}

export interface Message {
  id: number
  role: "user" | "assistant"
  content: string
  media_url: string | null
  created_at: string
}
