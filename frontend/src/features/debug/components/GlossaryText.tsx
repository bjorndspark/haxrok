import { splitGlossary } from "../present"
import type { GlossaryEntry } from "../types"

/** Text with each glossary term underlined and defined on hover. */
export function GlossaryText({ text, glossary }: { text: string; glossary: GlossaryEntry[] }) {
  return (
    <>
      {splitGlossary(text, glossary).map((part, i) =>
        part.term ? (
          <abbr
            key={i}
            title={part.term.definition}
            className="cursor-help underline decoration-info decoration-dotted underline-offset-2"
          >
            {part.text}
          </abbr>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  )
}
