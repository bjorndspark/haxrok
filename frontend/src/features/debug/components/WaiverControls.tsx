import { useState } from "react"

import { Badge } from "@/shared/ui/badge"
import { Button } from "@/shared/ui/button"
import { Prompt } from "@/shared/ui/prompt"

import { useMessages } from "../messages"
import type { WaiverHit } from "../types"
import { savedReviewer } from "../waivers"

/** What a card can do with its waiver; null props hide the control (an
 *  older run, or a debug run with no review file). */
export interface WaiverProps {
  hit: WaiverHit | null
  onAccept: ((reason: string, by: string) => void) | null
  onRemove: (() => void) | null
}

/** An accepted issue folded to one line: rank, title, reason, who, when. */
export function WaivedFold({
  rank,
  title,
  hit,
  onOpen,
}: {
  rank: number
  title: string
  hit: WaiverHit
  onOpen: () => void
}) {
  const t = useMessages().hypothesis.waiver
  const w = hit.waiver
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-expanded={false}
      className="flex w-full items-center gap-[1ch] border bg-card px-[1ch] py-1.5 text-left text-xs hover:bg-accent"
    >
      <Badge className="font-bold">#{rank}</Badge>
      <Badge tone="ok" bracket className="shrink-0">
        {t.accepted}
      </Badge>
      <span className="min-w-0 flex-1 truncate">
        <span className="text-muted-foreground line-through">{title}</span>{" "}
        <span>{t.because(w.reason)}</span>{" "}
        <span className="text-muted-foreground">
          {t.byOn(w.by, new Date(w.date).toLocaleDateString())}
        </span>
      </span>
      <span className="shrink-0" aria-hidden>
        ▸
      </span>
    </button>
  )
}

/** The card's bottom line: accept it, or the waiver it has (kept, or lapsed
 *  because its circuit changed) with a way to accept again or remove it. */
export function WaiverBar({
  hit,
  onAccept,
  onRemove,
  onFold,
}: WaiverProps & { onFold: (() => void) | null }) {
  const t = useMessages().hypothesis.waiver
  if (!hit) return onAccept && <AcceptForm label={t.accept} initialReason="" onAccept={onAccept} />
  const w = hit.waiver
  const when = new Date(w.date).toLocaleDateString()
  const lapsed = hit.lapsed_pins.length > 0
  return (
    <div className="space-y-2 border-t px-4 py-2.5 text-xs sm:px-5">
      <p>
        {lapsed ? (
          <span className="text-warn">{t.lapsed(hit.lapsed_pins)}</span>
        ) : (
          <Badge tone="ok" bracket>
            {t.accepted}
          </Badge>
        )}{" "}
        {t.because(w.reason)} <span className="text-muted-foreground">{t.byOn(w.by, when)}</span>
      </p>
      <div className="flex flex-wrap gap-[1ch]">
        {lapsed && onAccept && (
          <AcceptForm label={t.acceptAgain} initialReason={w.reason} onAccept={onAccept} bare />
        )}
        {!lapsed && onFold && (
          <Button size="sm" variant="outline" onClick={onFold}>
            {t.fold}
          </Button>
        )}
        {onRemove && (
          <Button size="sm" variant="outline" onClick={onRemove}>
            {t.remove}
          </Button>
        )}
      </div>
    </div>
  )
}

/** A button that opens a reason and name form; the name is remembered. */
function AcceptForm({
  label,
  initialReason,
  onAccept,
  bare = false,
}: {
  label: string
  initialReason: string
  onAccept: (reason: string, by: string) => void
  /** Inside WaiverBar, which draws its own border. */
  bare?: boolean
}) {
  const t = useMessages().hypothesis.waiver
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState(initialReason)
  const [by, setBy] = useState(savedReviewer)
  const ready = reason.trim() !== "" && by.trim() !== ""
  const body = open ? (
    <form
      className="w-full space-y-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (ready) onAccept(reason.trim(), by.trim())
      }}
    >
      <label className="block">
        <span className="block text-xs font-bold tracking-wider text-muted-foreground uppercase">
          {t.reason}
        </span>
        <Prompt
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder={t.reasonPlaceholder}
          maxLength={500}
          autoFocus
        />
      </label>
      <label className="block">
        <span className="block text-xs font-bold tracking-wider text-muted-foreground uppercase">
          {t.by}
        </span>
        <Prompt value={by} onChange={(e) => setBy(e.target.value)} maxLength={100} />
      </label>
      <div className="flex gap-[1ch]">
        <Button type="submit" size="sm" disabled={!ready}>
          {label}
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={() => setOpen(false)}>
          {t.cancel}
        </Button>
      </div>
    </form>
  ) : (
    <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
      {label}…
    </Button>
  )
  return bare ? body : <div className="border-t px-4 py-2.5 sm:px-5">{body}</div>
}
