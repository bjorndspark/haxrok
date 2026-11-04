import { markTokens, type MarkKind } from "../present"

const MARK_CLASS: Record<MarkKind, string> = { ref: "text-info", net: "text-net" }

/** Backend-written text with its refs in cyan and nets in magenta. Only for
 *  text the backend builds from the netlist, never the model's prose. */
export function MarkedText({
  text,
  tokens,
}: {
  text: string
  tokens: { ref: string[]; net: string[] }
}) {
  return (
    <>
      {markTokens(text, tokens).map((part, i) =>
        part.kind ? (
          <span key={i} className={MARK_CLASS[part.kind]}>
            {part.text}
          </span>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  )
}
