import { Plus, Trash2 } from "lucide-react"

import { Button } from "@/shared/ui/button"
import { cn } from "@/shared/lib/utils"

import { Composer } from "./components/Composer"
import { MessageList } from "./components/MessageList"
import { useChatUi } from "./store"
import { useChatData, useRemoveConversation, useSendMessage } from "./useChat"

export function ChatPage() {
  const { conversations, messages, activeConversationId } = useChatData()
  const { sending, draft, error, setActiveConversation, newChat } = useChatUi()
  const send = useSendMessage()
  const removeConversation = useRemoveConversation()

  return (
    <div className="flex h-screen">
      <aside className="flex w-56 flex-col border-r">
        <div className="border-b p-3">
          <Button variant="outline" size="sm" className="w-full" onClick={newChat}>
            <Plus /> New chat
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {conversations.map((c) => (
            <div
              key={c.id}
              className={cn(
                "group flex cursor-pointer items-center justify-between px-3 py-2 text-sm hover:bg-accent",
                c.id === activeConversationId && "bg-accent",
              )}
              onClick={() => setActiveConversation(c.id)}
            >
              <span className="truncate">{c.title || "Untitled"}</span>
              <button
                className="invisible text-muted-foreground group-hover:visible"
                onClick={(e) => {
                  e.stopPropagation()
                  void removeConversation(c.id)
                }}
                title="Delete conversation"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      </aside>

      <main className="flex flex-1 flex-col">
        {error ? (
          <div className="border-b bg-destructive/10 px-4 py-2 text-sm text-destructive">
            {error}
          </div>
        ) : null}
        <MessageList messages={messages} draft={draft} sending={sending} />
        <Composer onSend={(t, img) => void send(t, img)} disabled={sending} />
      </main>
    </div>
  )
}
