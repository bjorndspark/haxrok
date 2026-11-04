import { useState } from "react"

import { Button } from "@/shared/ui/button"
import { Prompt } from "@/shared/ui/prompt"

import type { Chat } from "../useDebug"

interface ChatText {
  suggestions: readonly string[]
  /** Shown while the answer is on its way. */
  reading: string
  ask: string
  askPlaceholder: string
}

/** A conversation: answered turns, the question in flight, suggested first
 *  questions while it is empty, and the prompt. Used for a simulation and
 *  for a finished run. */
export function ChatThread({ chat, text }: { chat: Chat; text: ChatText }) {
  const [question, setQuestion] = useState("")
  const busy = chat.pending !== null
  const send = (q: string) => {
    chat.ask(q)
    setQuestion("")
  }
  return (
    <div className="space-y-2 text-xs">
      {chat.turns.map((turn, i) => (
        <div key={i} className="space-y-1">
          <p className="font-bold">
            <span className="text-muted-foreground">&gt; </span>
            {turn.question}
          </p>
          <p className="leading-relaxed whitespace-pre-line">{turn.answer}</p>
        </div>
      ))}
      {chat.pending !== null && (
        <div className="space-y-1">
          <p className="font-bold">
            <span className="text-muted-foreground">&gt; </span>
            {chat.pending}
          </p>
          <p className="text-muted-foreground">
            <span className="animate-pulse">●</span> {text.reading}
          </p>
        </div>
      )}
      {chat.error && <p className="text-bad">{chat.error}</p>}
      {chat.turns.length === 0 && !busy && (
        <div className="flex flex-wrap gap-x-[2ch] gap-y-1">
          {text.suggestions.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => send(s)}
              className="text-info underline decoration-dotted underline-offset-2 hover:no-underline"
            >
              {s}
            </button>
          ))}
        </div>
      )}
      <form
        className="flex"
        onSubmit={(e) => {
          e.preventDefault()
          send(question)
        }}
      >
        <Prompt
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={text.askPlaceholder}
          maxLength={1000}
          aria-label={text.askPlaceholder}
        >
          <Button type="submit" size="sm" disabled={busy || !question.trim()}>
            {text.ask}
          </Button>
        </Prompt>
      </form>
    </div>
  )
}
