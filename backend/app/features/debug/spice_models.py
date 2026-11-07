"""Each IC's SPICE model: found, classified by simulator, kept on disk.

For every IC, in order:

1. the uploaded file the symbol's ``Sim.Library`` names (KiCad 6:
   ``Spice_Lib_File``), with ``Sim.Name`` picking the subcircuit. Uploaded
   files are read, never kept. Named but not uploaded, the web is tried and
   the status says "<file> not uploaded";
2. model files already kept under the part's datasheet key, so a part is
   downloaded once, not per run;
3. the part's product page: the schematic's ``Datasheet`` URL when it isn't
   the PDF itself, and for TI parts ``ti.com/product/<part>`` (the one
   site-specific adapter, see the plan). LCSC's record gives the brand and
   MPN but no product page link. Links to model files or named
   SPICE/PSpice/LTspice are followed; a ZIP keeps only its model text files.

Each model is classified from its text (``classify``): encrypted, then the
simulator its comments name, then PSpice-only or LTspice-only syntax, else
ngspice-ready, which is a guess the simulation step checks for real.
Network and disk sit behind ``Fetcher`` and ``SpiceModelStore``; misses
aren't kept, so a later run looks again.
"""

import asyncio
import io
import json
import os
import posixpath
import re
import shutil
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol
from urllib.parse import urljoin, urlsplit

from app.core.config import settings
from app.core.logging import get_logger
from app.features.debug.datasheet import Fetched, Fetcher, lcsc_record, part_key
from app.features.debug.design import Part
from app.features.debug.project import MODEL_SUFFIXES
from app.features.debug.schemas import DatasheetStatus, SpiceModelStatus

logger = get_logger(__name__)

Simulator = Literal["ngspice", "pspice", "ltspice", "encrypted"]

# Links worth following: model files, LTspice schematics and archives.
_LINK_SUFFIXES = (*MODEL_SUFFIXES, ".asc", ".zip")
_SPICE_WORD = re.compile(r"\b(?:p|lt|ng|h)?spice\b", re.IGNORECASE)
# Links that look like models but can't give one without a person.
_NO_MODEL_LINK = re.compile(r"login|signin|sign-in|register|request|ibis|tina", re.IGNORECASE)
_LINKS_PER_PAGE = 3
_ZIP_MAX_FILES = 20
_MODEL_MAX_BYTES = 5 * 1024 * 1024
_ZIP_MAX_TOTAL = 20 * 1024 * 1024
# A part may take LCSC's record, two product pages and three links, each
# with the fetcher's own timeout; this bounds all of them together.
_REQUESTS_PER_PART = 6

_MODEL_NAME = re.compile(r"^[ \t]*\.(subckt|model)[ \t]+(\S+)", re.IGNORECASE | re.MULTILINE)
_LTSPICE_ENCRYPTED = re.compile(rb"LTspice\s+Encrypted", re.IGNORECASE)
_SIMULATOR_NAMED = re.compile(r"\b(pspice|ltspice|ngspice)\b", re.IGNORECASE)
# Syntax ngspice reads only in a compatibility mode, or not at all.
_PSPICE_SYNTAX = re.compile(
    r"\bPARAMS:|^[ \t]*[EG]\S*[ \t].*\b(?:LAPLACE|FREQ|CHEBYSHEV)\b|\b(?:IF|STP)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
_LTSPICE_SYNTAX = re.compile(
    r"\b(?:rser|rpar|cpar|lser|vhigh)\s*=|^[ \t]*\.machine\b|\b(?:uplim|dnlim)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
_TI_SYMLINK = re.compile(r"ti\.com/.*/symlink/([a-z0-9][a-z0-9-]*)\.pdf", re.IGNORECASE)
TI_PRODUCT_URL = "https://www.ti.com/product/{name}"


@dataclass(frozen=True)
class ModelFile:
    name: str  # the file's name (uploaded: its path in the project)
    text: bytes
    path: str = ""  # where it's kept on the server; "" when not kept


def classify(text: bytes) -> Simulator:
    """Which simulator a model file is written for, from its text alone.
    ``ngspice`` means nothing says otherwise, not that ngspice loads it."""
    if b"\0" in text[:8192] or b"$CDNENCSTART" in text or _LTSPICE_ENCRYPTED.search(text[:4096]):
        return "encrypted"
    s = text.decode("utf-8", errors="replace")
    if s.count("�") > len(s) // 10:
        return "encrypted"  # binary without a NUL in its head
    lines = s.splitlines()
    comments = "\n".join(ln for ln in lines if ln.lstrip().startswith(("*", ";")))
    if named := _SIMULATOR_NAMED.search(comments):
        sim = named.group(1).lower()
        return "pspice" if sim == "pspice" else "ltspice" if sim == "ltspice" else "ngspice"
    code = "\n".join(ln for ln in lines if not ln.lstrip().startswith(("*", ";")))
    if _PSPICE_SYNTAX.search(code):
        return "pspice"
    if _LTSPICE_SYNTAX.search(code):
        return "ltspice"
    return "ngspice"


def model_names(text: bytes) -> tuple[list[str], list[str]]:
    """The ``.subckt`` names and the ``.model`` names, in file order."""
    found = _MODEL_NAME.findall(text.decode("utf-8", errors="replace"))
    return (
        [n for kind, n in found if kind.lower() == "subckt"],
        [n for kind, n in found if kind.lower() == "model"],
    )


def pick(
    files: Sequence[ModelFile], sim_name: str = "", mpn: str = ""
) -> tuple[ModelFile, str | None] | None:
    """The file and subcircuit to use: the one ``Sim.Name`` names, else one
    whose name starts the MPN (``TPS63802_TRANS`` for ``TPS63802DLAR``),
    else the first subcircuit, else the first ``.model``. An encrypted file
    with no readable name is still picked, so the status says so."""
    names = {f.name: model_names(f.text) for f in files}
    subckts = [(f, n) for f in files for n in names[f.name][0]]
    named = subckts + [(f, n) for f in files for n in names[f.name][1]]
    if sim_name:
        for f, n in named:
            if n.casefold() == sim_name.casefold():
                return f, n
    if mpn:
        for f, n in named:
            stem = re.split(r"[_.-]", n)[0]
            if len(stem) >= 4 and mpn.upper().startswith(stem.upper()):
                return f, n
    if named:
        return named[0]
    encrypted = [f for f in files if classify(f.text) == "encrypted"]
    return (encrypted[0], None) if encrypted else None


# --- what was downloaded ----------------------------------------------------------


def model_files(got: Fetched) -> list[tuple[str, bytes]]:
    """The model files in a download: a ZIP's model text files (by
    basename, capped in count and size), or the body itself when it's a
    model file. Anything else, such as an HTML page, gives none."""
    if got.body.startswith(b"PK\x03\x04"):
        return _unzip(got.body)
    name = _safe_name(PurePosixPath(urlsplit(got.url).path).name or "model.lib")
    if len(got.body) > _MODEL_MAX_BYTES or got.body.lstrip()[:1] == b"<":
        return []
    if name.lower().endswith(MODEL_SUFFIXES) or any(model_names(got.body)):
        return [(name, got.body)]
    return []


def _unzip(body: bytes) -> list[tuple[str, bytes]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        return []
    files: dict[str, bytes] = {}
    total = 0
    for info in archive.infolist():
        # Basenames only, so a member can't land outside the part's folder.
        name = _safe_name(_basename(info.filename))
        if info.is_dir() or not name.lower().endswith(MODEL_SUFFIXES) or name in files:
            continue
        if len(files) >= _ZIP_MAX_FILES or info.file_size > _MODEL_MAX_BYTES:
            continue
        try:
            with archive.open(info) as f:
                data = f.read(_MODEL_MAX_BYTES + 1)  # the header's size can lie
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError):
            continue  # corrupt, or a password-protected member
        if len(data) > _MODEL_MAX_BYTES or total + len(data) > _ZIP_MAX_TOTAL:
            continue
        total += len(data)
        files[name] = data
    return list(files.items())


def _safe_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).lstrip(".")
    return safe or "model.lib"


# --- product pages ----------------------------------------------------------------


class _Links(HTMLParser):
    """Every ``<a href>`` on a page with its text, title and data-navtitle."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._open: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        a = {k: v or "" for k, v in attrs}
        if a.get("href"):
            label = [a.get("title", ""), a.get("data-navtitle", ""), a.get("aria-label", "")]
            self._open.append((a["href"], label))

    def handle_data(self, data: str) -> None:
        if self._open:
            self._open[-1][1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._open:
            href, label = self._open.pop()
            self.links.append((href, " ".join(" ".join(label).split())))


def model_links(html: str, page_url: str, part_number: str = "") -> list[str]:
    """Links on a product page that may lead to a model: model files and
    archives by suffix, or links named SPICE/PSpice/LTspice. Best first: an
    unencrypted model of this part over a simulator's download page."""
    parser = _Links()
    parser.feed(html)
    stem = re.match(r"[A-Za-z]+\d+", part_number)
    scored: dict[str, int] = {}
    for href, text in parser.links:
        url = urljoin(page_url, href.strip())
        path = urlsplit(url).path.lower()
        if urlsplit(url).scheme not in ("http", "https") or url in scored:
            continue
        if not (path.endswith(_LINK_SUFFIXES) or _SPICE_WORD.search(text)):
            continue
        if _NO_MODEL_LINK.search(text) or _NO_MODEL_LINK.search(path):
            continue
        low = text.lower()
        score = 2 * ("model" in low) + ("unencrypted" in low) + path.endswith(_LINK_SUFFIXES)
        score -= 2 * ("encrypted" in low.replace("unencrypted", ""))
        if stem and stem.group().lower() in (low + path):
            score += 2
        scored[url] = score
    return sorted(scored, key=lambda u: -scored[u])


def ti_product_names(part: Part, mpn: str, datasheet_urls: Sequence[str]) -> list[str]:
    """Names for ``ti.com/product/<name>``: the datasheet's ``symlink``
    name (``tlv755p``), then the MPN's letters and digits (``TPS63802``
    from ``TPS63802DLAR``; the orderable number gives a 404)."""
    names = [m.group(1) for u in datasheet_urls if (m := _TI_SYMLINK.search(u))]
    if stem := re.match(r"[A-Za-z]+\d+", mpn or part.value):
        names.append(stem.group())
    return list(dict.fromkeys(n.upper() for n in names))[:2]


def _is_ti(brand: str, urls: Sequence[str]) -> bool:
    b = brand.strip().lower()
    if b in ("ti", "texas instruments") or b.startswith(("ti ", "ti(", "texas instr")):
        return True
    return any((urlsplit(u).hostname or "").lower().endswith("ti.com") for u in urls if u)


# --- the uploaded Sim.Library file ------------------------------------------------


def uploaded_library(sim_library: str, files: dict[str, bytes], root: str) -> str | None:
    """The uploaded file ``Sim.Library`` names: its path from the root
    sheet's folder (``${KIPRJMOD}`` is that folder), else the only uploaded
    model file with its basename. None when it wasn't uploaded."""
    name = re.sub(r"^(\$\{KIPRJMOD\}|\$\(KIPRJMOD\))[/\\]?", "", sim_library).replace("\\", "/")
    rel = posixpath.normpath(str(PurePosixPath(root).parent / name)).replace("\\", "/")
    if rel in files:
        return rel
    base = _basename(name).lower()
    same = [p for p in files if p.lower().endswith(MODEL_SUFFIXES) and _basename(p).lower() == base]
    return same[0] if len(same) == 1 else None


def _basename(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).name


# --- kept models ------------------------------------------------------------------


@dataclass(frozen=True)
class Kept:
    files: list[ModelFile]
    url: str


class SpiceModelStore:
    """Each part's model files in ``<key>/``, with ``record.json`` saying
    where they came from. Kept until deleted by hand."""

    RECORD = "record.json"

    def __init__(self, root: Path) -> None:
        self.root = root

    def get(self, key: str) -> Kept | None:
        folder = self._folder(key)
        try:
            record = json.loads((folder / self.RECORD).read_text("utf-8"))
            paths = sorted(p for p in folder.iterdir() if p.is_file() and p.name != self.RECORD)
            files = [ModelFile(p.name, p.read_bytes(), str(p)) for p in paths]
        except (OSError, ValueError):
            return None
        return Kept(files, str(record.get("url", ""))) if files else None

    def put(self, key: str, files: Sequence[tuple[str, bytes]], url: str) -> Kept:
        """Write the files and the record into a fresh folder, then move it
        into place, so a crash in between leaves no half-kept model."""
        self.root.mkdir(parents=True, exist_ok=True)
        folder = self._folder(key)
        tmp = Path(tempfile.mkdtemp(dir=self.root, prefix=".tmp-"))
        try:
            for name, data in files:
                (tmp / name).write_bytes(data)
            (tmp / self.RECORD).write_text(json.dumps({"source": "web", "url": url}), "utf-8")
            if folder.exists():
                shutil.rmtree(folder)  # a folder without a record, from an older run
            os.replace(tmp, folder)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return Kept([ModelFile(n, d, str(folder / n)) for n, d in files], url)

    def _folder(self, key: str) -> Path:
        return self.root / re.sub(r"[^A-Za-z0-9._-]+", "_", key)


# --- the finder -------------------------------------------------------------------


class SpiceModelFinder(Protocol):
    async def find(
        self,
        parts: Sequence[Part],
        datasheets: Sequence[DatasheetStatus],
        files: dict[str, bytes],
        root: str,
    ) -> list[SpiceModelStatus]: ...


class SpiceModels:
    """Finds and classifies each part's SPICE model; see the module docstring."""

    def __init__(
        self, fetcher: Fetcher, store: SpiceModelStore, part_timeout_s: float | None = None
    ) -> None:
        self.fetcher = fetcher
        self.store = store
        self.part_timeout_s = part_timeout_s or settings.DATASHEET_TIMEOUT_S * _REQUESTS_PER_PART

    async def find(
        self,
        parts: Sequence[Part],
        datasheets: Sequence[DatasheetStatus],
        files: dict[str, bytes],
        root: str,
    ) -> list[SpiceModelStatus]:
        """One status per part, in order. Web lookups run in parallel, and
        parts that share a key (two of the same LDO) share one."""
        sheets = {s.ref: s for s in datasheets}
        tasks: dict[str, asyncio.Task[SpiceModelStatus]] = {}
        pending: list[tuple[Part, SpiceModelStatus | None, str | None, str]] = []
        for part in parts:
            note = ""
            if part.sim_library:
                rel = uploaded_library(part.sim_library, files, root)
                if rel is not None:
                    upload = ModelFile(rel, files[rel])
                    pending.append((part, _status(part, [upload], "upload", None), None, ""))
                    continue
                note = f"{_basename(part.sim_library)} not uploaded"
            key = part_key(part)
            if key is not None and key not in tasks:
                tasks[key] = asyncio.create_task(self._find_one(part, key, sheets.get(part.ref)))
            pending.append((part, None, key, note))
        statuses = []
        for part, done, key, note in pending:
            if done is None:
                done = await tasks[key] if key is not None else _nothing_to_look_up(part)
                done = done.model_copy(update={"ref": part.ref, "value": part.value})
                if note:
                    done.reason = f"{note}; {done.reason}" if done.reason else note
            statuses.append(done)
        for s in statuses:
            logger.info(
                "spice model %s (%s): %s %s %s", s.ref, s.value, s.status, s.file or "", s.reason
            )
        return statuses

    async def _find_one(
        self, part: Part, key: str, sheet: DatasheetStatus | None
    ) -> SpiceModelStatus:
        try:
            async with asyncio.timeout(self.part_timeout_s):
                return await self._lookup(part, key, sheet)
        except TimeoutError:
            return _none(part, f"timed out after {self.part_timeout_s:g} s")

    async def _lookup(
        self, part: Part, key: str, sheet: DatasheetStatus | None
    ) -> SpiceModelStatus:
        if kept := await asyncio.to_thread(self.store.get, key):
            return _status(part, kept.files, "web", kept.url)
        brand, mpn = part.manufacturer, part.mpn
        if part.lcsc and not (brand and mpn):
            record = await lcsc_record(self.fetcher, part.lcsc) or {}
            brand = brand or str(record.get("brandNameEn") or "")
            mpn = mpn or str(record.get("productModel") or "")
        pages: list[str] = []
        # The schematic's URL is a product page only when it didn't give the PDF.
        if part.datasheet and not (sheet and sheet.source == "schematic"):
            pages.append(part.datasheet)
        sheet_urls = [part.datasheet, sheet.url if sheet and sheet.url else ""]
        if _is_ti(brand, sheet_urls):
            pages += [
                TI_PRODUCT_URL.format(name=n) for n in ti_product_names(part, mpn, sheet_urls)
            ]
        if not pages:
            return _none(part, f"no product page known for {brand or 'this part'}", mpn)
        for page in pages:
            got = await self.fetcher.get(page)
            if got is None:
                continue
            found = model_files(got)
            url = page
            if not found:
                html = got.body.decode("utf-8", errors="replace")
                for link in model_links(html, got.url, mpn or part.value)[:_LINKS_PER_PAGE]:
                    if (dl := await self.fetcher.get(link)) and (found := model_files(dl)):
                        url = link
                        break
            if found and pick([ModelFile(n, d) for n, d in found], part.sim_name, mpn):
                kept = await asyncio.to_thread(self.store.put, key, found, url)
                return _status(part, kept.files, "web", url, mpn)
        hosts = ", ".join(dict.fromkeys(urlsplit(p).hostname or p for p in pages))
        return _none(part, f"no model found on {hosts}", mpn)


def _status(
    part: Part,
    files: Sequence[ModelFile],
    source: Literal["upload", "web"],
    url: str | None,
    mpn: str = "",
) -> SpiceModelStatus:
    picked = pick(files, part.sim_name, mpn or part.mpn)
    if picked is None:
        names = ", ".join(f.name for f in files)
        return _none(part, f"{names} has no .subckt or .model", mpn)
    file, subckt = picked
    reason = ""
    if part.sim_name and subckt and subckt.casefold() != part.sim_name.casefold():
        reason = f"{part.sim_name} not in the file; used {subckt}"
    return SpiceModelStatus(
        ref=part.ref,
        value=part.value,
        status=classify(file.text),
        source=source,
        url=url or None,
        file=file.name,
        subckt=subckt,
        part_number=mpn or part.mpn or None,
        reason=reason,
        path=file.path,
    )


def _nothing_to_look_up(part: Part) -> SpiceModelStatus:
    if part.value in ("", "~") or not any(c.isdigit() for c in part.value):
        return SpiceModelStatus(
            ref=part.ref, value=part.value, status="skipped", reason="generic value"
        )
    return _none(part, "no part number or datasheet URL")


def _none(part: Part, reason: str, mpn: str = "") -> SpiceModelStatus:
    return SpiceModelStatus(
        ref=part.ref,
        value=part.value,
        status="none",
        part_number=mpn or part.mpn or None,
        reason=reason,
    )
