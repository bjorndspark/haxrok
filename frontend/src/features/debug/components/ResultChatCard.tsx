import { Card } from "@/shared/ui/card"
import { Title } from "@/shared/ui/title"

import { useMessages } from "../messages"
import { isReview } from "../present"
import type { CheckedAnalysis, DesignSummary, Run } from "../types"
import { useResultChat } from "../useDebug"
import { ChatThread } from "./ChatThread"

/** Follow-up questions about a finished run's findings. */
export function ResultChatCard({
  run,
  summary,
  result,
}: {
  run: Run
  summary: DesignSummary
  result: CheckedAnalysis
}) {
  const chat = useResultChat(run, summary, result)
  const t = useMessages().resultChat
  return (
    <Card className="space-y-3 p-4 sm:p-5">
      <Title as="h3">{t.title}</Title>
      <ChatThread
        chat={chat}
        text={{ ...t, suggestions: t.suggestions[isReview(summary) ? "review" : "debug"] }}
      />
    </Card>
  )
}
