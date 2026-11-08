import type { CitedLine } from "../present"

/** A datasheet fact or deviation as `ds4 · p.44 text`, the page linking to
 *  the PDF at that page when its URL is known. */
export function DatasheetLineText({ line }: { line: CitedLine }) {
  const page = `p.${String(line.page)}`
  return (
    <span>
      <span className="font-bold text-muted-foreground">{line.id}</span>
      {" · "}
      {line.url ? (
        <a href={line.url} target="_blank" rel="noreferrer" className="text-info hover:underline">
          {page}
        </a>
      ) : (
        <span className="text-muted-foreground">{page}</span>
      )}{" "}
      {line.text}
    </span>
  )
}
