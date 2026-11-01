import { Volume2 } from "lucide-react"
import { useEffect, useRef } from "react"

import { apiUrl } from "@/shared/api/client"
import { Button } from "@/shared/ui/button"
import { cn } from "@/shared/lib/utils"

import type { Message } from "../types"
import { useReadAloud } from "../useChat"

interface Props {
  messages: Message[]
  /** Partial assistant text while a reply streams. */
  draft: string
  sending: boolean
}

export function MessageList({ messages, draft, sending }: Props) {
  const endRef = useRef<HTMLDivElement>(null)
  const readAloud = useReadAloud()

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages.length, draft])

  return (
    <div className="flex-1 space-y-4 overflow-y-auto p-4">
      {messages.map((m) => (
        <div key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
          <div
            className={cn(
              "max-w-[75%] rounded-lg px-4 py-2 text-sm",
              m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted text-foreground",
            )}
          >
            {m.media_url ? (
              <img
                src={apiUrl(m.media_url)}
                alt={m.content}
                className="mb-2 max-w-full rounded-md"
              />
            ) : null}
            <p className="whitespace-pre-wrap">{m.content}</p>
            {m.role === "assistant" && !m.media_url ? (
              <Button
                variant="ghost"
                size="icon"
                className="mt-1 h-6 w-6"
                onClick={() => void readAloud(m.content)}
                title="Read aloud"
              >
                <Volume2 />
              </Button>
            ) : null}
          </div>
        </div>
      ))}

      {draft || sending ? (
        <div className="flex justify-start">
          <div className="max-w-[75%] rounded-lg bg-muted px-4 py-2 text-sm text-foreground">
            <p className="whitespace-pre-wrap">{draft || "…"}</p>
          </div>
        </div>
      ) : null}

      <div ref={endRef} />
    </div>
  )
}
