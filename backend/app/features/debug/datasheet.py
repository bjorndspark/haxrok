"""Each IC's datasheet PDF: found, downloaded once, kept on disk.

For every IC, in order:

1. a PDF already kept under the part's key (its LCSC number, else its MPN,
   else a hash of its datasheet URL), so a part is downloaded once, not per
   run;
2. the schematic's own ``Datasheet`` URL, when it serves a PDF;
3. LCSC's product record for the part's LCSC number, whose ``pdfUrl`` is the
   PDF. The endpoint is unofficial, so anything unexpected there is "not
   found", never an error. LCSC's keyword search is behind a bot wall, so a
   part with only an MPN can't be looked up there.

An IC with nothing to look up and a generic value ("~", "LDO") is skipped.
The URLs come from the uploaded schematic, so ``HttpFetcher`` only reaches
public addresses. Network and disk sit behind ``Fetcher`` and
``DatasheetStore`` so tests fake them.
"""

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.features.debug.design import Part
from app.features.debug.schemas import DatasheetStatus

logger = get_logger(__name__)

Source = Literal["schematic", "lcsc"]

LCSC_PRODUCT_URL = "https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={code}"
_MAX_REDIRECTS = 5
# A part may take a request for its URL, one for LCSC's record and one for
# the PDF, each with its own timeout; this bounds all of them together.
_REQUESTS_PER_PART = 3


@dataclass(frozen=True)
class Fetched:
    """A successful (200) response body."""

    url: str  # after redirects
    content_type: str
    body: bytes


class Fetcher(Protocol):
    async def get(self, url: str) -> Fetched | None:
        """The body at ``url``, following redirects; None on any failure."""
        ...


Resolver = Callable[[str, int], Awaitable[list[str]]]
"""(host, port) -> the IP addresses it resolves to."""


async def _resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class HttpFetcher:
    """GETs over httpx, only to public addresses, with a size cap.

    Redirects are followed by hand so each hop's host is checked too. The
    check resolves the host before httpx does, so DNS that changes in
    between (rebinding) isn't caught; fine for a local tool, not for a
    public one.
    """

    def __init__(
        self,
        timeout_s: float | None = None,
        max_bytes: int | None = None,
        resolve: Resolver = _resolve,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout_s = timeout_s or settings.DATASHEET_TIMEOUT_S
        self.max_bytes = max_bytes or settings.DATASHEET_MAX_BYTES
        self.resolve = resolve
        self.transport = transport

    async def get(self, url: str) -> Fetched | None:
        headers = {"User-Agent": user_agent()}
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s, headers=headers, transport=self.transport
            ) as client:
                for _ in range(_MAX_REDIRECTS + 1):
                    if not await self._is_public(url):
                        logger.info("datasheet fetch refused, not a public address: %s", url)
                        return None
                    async with client.stream("GET", url) as r:
                        if r.is_redirect and "location" in r.headers:
                            url = str(r.url.join(r.headers["location"]))
                            continue
                        if r.status_code != 200:
                            logger.info("datasheet fetch got %s: %s", r.status_code, url)
                            return None
                        body = bytearray()
                        async for chunk in r.aiter_bytes():
                            body += chunk
                            if len(body) > self.max_bytes:
                                logger.info(
                                    "datasheet fetch over %d bytes: %s", self.max_bytes, url
                                )
                                return None
                        return Fetched(str(r.url), r.headers.get("content-type", ""), bytes(body))
                logger.info("datasheet fetch: too many redirects: %s", url)
        except httpx.HTTPError as e:
            logger.info("datasheet fetch failed (%s): %s", type(e).__name__, url)
        return None

    async def _is_public(self, url: str) -> bool:
        try:
            parsed = httpx.URL(url)
        except httpx.InvalidURL:
            return False
        if parsed.scheme not in ("http", "https") or not parsed.host:
            return False
        try:
            addresses = await self.resolve(parsed.host, parsed.port or 443)
        except OSError:
            return False
        return bool(addresses) and all(ipaddress.ip_address(a).is_global for a in addresses)


def user_agent() -> str:
    """The app's own name, ASCII-only (headers are Latin-1): LCSC refuses
    httpx's default agent but answers a named client."""
    name = unicodedata.normalize("NFKD", settings.PROJECT_NAME).encode("ascii", "ignore")
    return f"{re.sub(r'[^A-Za-z0-9]+', '-', name.decode()).strip('-') or 'app'}-datasheets/1.0"


@dataclass(frozen=True)
class Stored:
    path: Path
    source: Source
    url: str


class DatasheetStore:
    """PDFs on disk as ``<key>.pdf``, with ``<key>.json`` saying where each
    came from. Kept until deleted by hand."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def get(self, key: str) -> Stored | None:
        pdf, meta = self._paths(key)
        try:
            data = json.loads(meta.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not pdf.is_file() or data.get("source") not in ("schematic", "lcsc"):
            return None
        return Stored(pdf, data["source"], str(data.get("url", "")))

    def put(self, key: str, body: bytes, source: Source, url: str) -> Stored:
        """Write atomically: the PDF first, then the record that makes it
        count, so a crash in between leaves no half-kept datasheet."""
        pdf, meta = self._paths(key)
        self.root.mkdir(parents=True, exist_ok=True)
        _write_atomic(pdf, body)
        _write_atomic(meta, json.dumps({"source": source, "url": url}).encode())
        return Stored(pdf, source, url)

    def _paths(self, key: str) -> tuple[Path, Path]:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", key)
        return self.root / f"{safe}.pdf", self.root / f"{safe}.json"


def _write_atomic(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class DatasheetFinder(Protocol):
    async def find(self, parts: Sequence[Part]) -> list[DatasheetStatus]: ...


class Datasheets:
    """Finds each part's datasheet PDF; see the module docstring."""

    def __init__(
        self, fetcher: Fetcher, store: DatasheetStore, part_timeout_s: float | None = None
    ) -> None:
        self.fetcher = fetcher
        self.store = store
        self.part_timeout_s = part_timeout_s or settings.DATASHEET_TIMEOUT_S * _REQUESTS_PER_PART

    async def find(self, parts: Sequence[Part]) -> list[DatasheetStatus]:
        """One status per part, in order, looked up in parallel. Parts that
        share a key (two of the same LDO) share one lookup."""
        tasks: dict[str, asyncio.Task[DatasheetStatus]] = {}
        pending: list[tuple[Part, str | None]] = []
        for part in parts:
            key = _key(part)
            if key is not None and key not in tasks:
                tasks[key] = asyncio.create_task(self._find_one(part, key))
            pending.append((part, key))
        statuses = []
        for part, key in pending:
            if key is None:
                statuses.append(_nothing_to_look_up(part))
                continue
            found = await tasks[key]
            statuses.append(found.model_copy(update={"ref": part.ref, "value": part.value}))
        for s in statuses:
            logger.info("datasheet %s (%s): %s %s", s.ref, s.value, s.status, s.reason or s.url)
        return statuses

    async def _find_one(self, part: Part, key: str) -> DatasheetStatus:
        try:
            async with asyncio.timeout(self.part_timeout_s):
                return await self._lookup(part, key)
        except TimeoutError:
            return _not_found(part, f"timed out after {self.part_timeout_s:g} s")

    async def _lookup(self, part: Part, key: str) -> DatasheetStatus:
        if hit := self.store.get(key):
            return _found(part, hit)
        tried: list[str] = []
        if part.datasheet:
            if pdf := await self._pdf(part.datasheet):
                return _found(part, await self._keep(key, pdf, "schematic", part.datasheet))
            tried.append("the schematic's URL gave no PDF")
        if not part.lcsc:
            tried.append("no LCSC number to look up")
        elif not (url := await self._lcsc_pdf_url(part.lcsc)):
            tried.append(f"LCSC lists no PDF for {part.lcsc}")
        elif pdf := await self._pdf(url):
            return _found(part, await self._keep(key, pdf, "lcsc", url))
        else:
            tried.append("LCSC's PDF link gave no PDF")
        return _not_found(part, "; ".join(tried))

    async def _pdf(self, url: str) -> bytes | None:
        got = await self.fetcher.get(url)
        return got.body if got is not None and got.body.startswith(b"%PDF") else None

    async def _keep(self, key: str, body: bytes, source: Source, url: str) -> Stored:
        return await asyncio.to_thread(self.store.put, key, body, source, url)

    async def _lcsc_pdf_url(self, code: str) -> str | None:
        got = await self.fetcher.get(LCSC_PRODUCT_URL.format(code=code))
        if got is None:
            return None
        try:
            url = json.loads(got.body)["result"]["pdfUrl"]
        except (ValueError, KeyError, TypeError):
            return None
        return url if isinstance(url, str) and url.startswith("https://") else None


def _key(part: Part) -> str | None:
    """What a part's PDF is kept under; None when there's nothing to look up."""
    if part.lcsc:
        return f"lcsc-{part.lcsc}"
    if part.mpn:
        return f"mpn-{part.mpn}"
    if part.datasheet:
        return f"url-{hashlib.sha256(part.datasheet.encode()).hexdigest()[:16]}"
    return None


def _nothing_to_look_up(part: Part) -> DatasheetStatus:
    if part.value in ("", "~") or not any(c.isdigit() for c in part.value):
        return DatasheetStatus(
            ref=part.ref, value=part.value, status="skipped", reason="generic value"
        )
    return _not_found(part, "no datasheet URL or part number")


def _found(part: Part, stored: Stored) -> DatasheetStatus:
    return DatasheetStatus(
        ref=part.ref,
        value=part.value,
        status="found",
        source=stored.source,
        url=stored.url,
        part_number=part.lcsc or part.mpn or None,
        path=str(stored.path),
    )


def _not_found(part: Part, reason: str) -> DatasheetStatus:
    return DatasheetStatus(
        ref=part.ref,
        value=part.value,
        status="not_found",
        part_number=part.lcsc or part.mpn or None,
        reason=reason,
    )
