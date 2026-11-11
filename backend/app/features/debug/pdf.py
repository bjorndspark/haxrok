"""A datasheet PDF's text, page by page, cut to what one model call can read.

A datasheet up to ``budget`` characters is read whole. A longer one (a
microcontroller's runs to hundreds of pages) is cut to that budget by page,
preferring pages whose headings name what a schematic review needs: pin
descriptions, limits, electrical characteristics, the typical application,
boot, strapping pins and reset. Package drawings, ordering codes and revision history go
first.

pypdfium2 reads every page's plain text fast (RP2040's 642 pages in about
half a second), which is enough to choose pages; pdfplumber then reads the
chosen pages again for their tables, rendered as ``a | b | c`` rows so a pin
table keeps its columns. Page numbers are the PDF's own (1-based), so
``<url>#page=N`` opens the page a fact cites.

PDFium isn't thread-safe: two reads at once in one process crash it
(segfault), so ``read`` holds a process-wide lock. Callers run it in a
worker thread (``asyncio.to_thread``) and may start several; they queue.
"""

import re
import threading
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import pypdfium2

# ~30k tokens: room for the prompt and the answer in any current model's
# context, and a call that answers in about a minute.
BUDGET_CHARS = 120_000

# Headings that mark pages a review reads (core first, then useful), and
# pages it never needs. Matched against heading lines only (numbered sections,
# "Table N." captions and short lines), so a register table's "Reset" column
# on every page of a reference manual doesn't make it a reset section.
_CORE = re.compile(
    r"pin (description|definition|configuration|function|assignment|specification)s?|"
    r"pinout|absolute maximum|recommended operating|operating conditions|"
    r"electrical (characteristics|specifications)|typical application|"
    r"application (circuit|information)|boot (mode|configuration)|power-on reset|"
    r"reset pin|nrst|strapping",
    re.IGNORECASE,
)
_USEFUL = re.compile(
    r"boot|reset|power supply|power-up|crystal|oscillator|soft[- ]start|undervoltage|"
    r"thermal|characteristics|feedback|(input|output) voltage",
    re.IGNORECASE,
)
_UNWANTED = re.compile(
    r"package (information|outline|dimensions|mechanical)|mechanical data|"
    r"ordering information|revision history|tape and reel|land pattern|"
    r"important notice",
    re.IGNORECASE,
)
_NUMBERED = re.compile(r"^\s*(\d+(\.\d+)*\.?|table \d+\.?)\s+\S", re.IGNORECASE)
_TOC_LEADER = re.compile(r"\.{4,}|(\. ){4,}")  # "Boot modes ........ 12"
_CONTINUED = re.compile(r"\(continued\)", re.IGNORECASE)
_HEADING_MAX = 90  # characters; longer lines are prose, not headings
_SHORT_HEADING = 40  # an unnumbered line this short may be a heading

_PDFIUM = threading.Lock()  # see the module docstring


@dataclass(frozen=True)
class Page:
    number: int  # 1-based, as a PDF viewer counts
    text: str


def read(path: Path, budget: int = BUDGET_CHARS) -> list[Page]:
    """The pages to send for one datasheet, in page order, within
    ``budget`` characters. Empty for a scanned PDF (no text layer). Raises
    whatever the PDF libraries raise on a broken file. One read at a time
    per process."""
    with _PDFIUM:
        return _read(path, budget)


def _read(path: Path, budget: int) -> list[Page]:
    plain = _plain_text(path)
    chosen = choose(plain, budget)
    if not chosen:
        return []
    with pdfplumber.open(path) as pdf:
        pages = [Page(n, _with_tables(pdf.pages[n - 1])) for n in chosen]
    # Rendered tables can run a little longer than the plain text they
    # replace; drop the lowest-ranked pages again if they now overflow.
    while sum(len(p.text) for p in pages) > budget and len(pages) > 1:
        drop = next(n for n in reversed(_ranked(plain)) if n in {p.number for p in pages})
        pages = [p for p in pages if p.number != drop]
    return pages


def choose(plain: list[str], budget: int) -> list[int]:
    """Page numbers (1-based, ascending) to send, given each page's plain
    text: all of them when they fit, else the best-ranked that fit."""
    if sum(len(t) for t in plain) <= budget:
        return [n for n, t in enumerate(plain, start=1) if t.strip()]
    chosen: list[int] = []
    used = 0
    for n in _ranked(plain):
        size = len(plain[n - 1])
        if size and used + size <= budget:
            chosen.append(n)
            used += size
    return sorted(chosen)


def _ranked(plain: list[str]) -> list[int]:
    """Page numbers, best first: core headings, then useful ones, then the
    rest in order; unwanted and empty pages last. Page 1 (the feature
    summary) always leads, and a "(continued)" page ranks with the page it
    continues."""
    scores: list[int] = []
    for i, text in enumerate(plain):
        score = _score(text)
        if i > 0 and _CONTINUED.search(text):
            score = max(score, scores[i - 1])  # "Table 11. Pin definitions (continued)"
        scores.append(score)
    if scores and plain[0].strip():
        scores[0] = 100
    order = sorted(range(len(plain)), key=lambda i: (-scores[i], i))
    return [i + 1 for i in order]


def _score(text: str) -> int:
    if not text.strip():
        return -100
    headings = [
        line
        for raw in text.splitlines()
        if (line := raw.strip())
        and len(line) <= _HEADING_MAX
        and not _TOC_LEADER.search(line)
        and (_NUMBERED.match(line) or len(line) <= _SHORT_HEADING)
    ]
    if any(_UNWANTED.search(h) for h in headings):
        return -10
    numbered = [h for h in headings if _NUMBERED.match(h)]
    if any(_CORE.search(h) for h in headings):
        return 10
    if any(_USEFUL.search(h) for h in numbered):
        return 3
    return 0


def _plain_text(path: Path) -> list[str]:
    pdf = pypdfium2.PdfDocument(path)
    try:
        texts = []
        for page in pdf:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range().replace("\r\n", "\n"))
            textpage.close()
            page.close()
        return texts
    finally:
        pdf.close()


def _with_tables(page: pdfplumber.page.Page) -> str:
    """The page's text with each ruled table rendered as rows of cells."""
    tables = page.find_tables()
    outside = page
    for table in tables:
        # strict=False: a table's box can stick out past the page edge.
        outside = outside.outside_bbox(table.bbox, strict=False)
    parts = [outside.extract_text() or ""]
    for table in tables:
        rows = [
            " | ".join(" ".join((cell or "").split()) for cell in row) for row in table.extract()
        ]
        parts.append("\n".join(r for r in rows if r.strip(" |")))
    return "\n\n".join(p for p in parts if p.strip())
