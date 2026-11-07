"""Finding each IC's datasheet PDF: the part-number fields the netlist
parser keeps, the lookup order (kept file, schematic URL, LCSC), and the
fetcher's limits. The network is faked: a dict of URLs, or an httpx
MockTransport."""

import asyncio
import json

import httpx
import pytest

from app.core.config import settings
from app.features.debug import datasheet
from app.features.debug.datasheet import (
    LCSC_PRODUCT_URL,
    Datasheets,
    DatasheetStore,
    Fetched,
    HttpFetcher,
)
from app.features.debug.design import Part, parse_netlist

PDF = b"%PDF-1.4 fake"

NETLIST = """(export (version "E")
  (components
    (comp (ref "U1") (value "MCP73031") (footprint "SOT-23-5")
      (datasheet "http://ww1.microchip.com/downloads/en/DeviceDoc/20001984g.pdf")
      (fields (field (name "LCSC Part #") "c424093") (field (name "Datasheet") "x"))
      (libsource (lib "x") (part "y") (description "")))
    (comp (ref "U2") (value "RT9013-33GB") (footprint "SOT-23-5") (datasheet "~")
      (fields (field (name "MFR. Part #") "RT9013-33GB") (field (name "JLCPCB Part #") "C47773"))
      (libsource (lib "x") (part "y") (description "")))
    (comp (ref "U3") (value "ICM-42670-P") (footprint "LGA-14")
      (fields (field (name "MANUFACTURER") "TDK InvenSense") (field (name "MPN") "ICM-42670-P")
        (field (name "LCSC") "~"))
      (libsource (lib "x") (part "y") (description ""))))
  (nets))
"""


def test_parse_netlist_keeps_datasheet_url_and_part_numbers():
    parts = parse_netlist(NETLIST).parts
    assert parts["U1"].datasheet.endswith("20001984g.pdf")
    assert parts["U1"].lcsc == "C424093"
    assert (parts["U2"].datasheet, parts["U2"].lcsc, parts["U2"].mpn) == (
        "",
        "C47773",
        "RT9013-33GB",
    )
    assert (parts["U3"].lcsc, parts["U3"].mpn, parts["U3"].manufacturer) == (
        "",
        "ICM-42670-P",
        "TDK InvenSense",
    )


class FakeFetcher:
    """Serves bodies by URL; anything else fails like a 404."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    async def get(self, url: str) -> Fetched | None:
        self.calls.append(url)
        body = self.pages.get(url)
        return None if body is None else Fetched(url, "application/octet-stream", body)


def _lcsc(code: str, pdf_url: str | None) -> tuple[str, bytes]:
    result = {"productCode": code, "pdfUrl": pdf_url} if pdf_url else {}
    return LCSC_PRODUCT_URL.format(code=code), json.dumps({"code": 200, "result": result}).encode()


def _part(ref: str, value: str, **kw: str) -> Part:
    return Part(ref, value, "lib:part", "", **kw)


async def test_schematic_url_first_then_kept_on_disk(tmp_path):
    url = "https://example.com/ds.pdf"
    fetcher = FakeFetcher({url: PDF})
    finder = Datasheets(fetcher, DatasheetStore(tmp_path))
    [status] = await finder.find([_part("U1", "MCP73031", datasheet=url, lcsc="C424093")])
    assert (status.status, status.source, status.url) == ("found", "schematic", url)
    assert status.part_number == "C424093"
    assert (tmp_path / "lcsc-C424093.pdf").read_bytes() == PDF

    fetcher.calls.clear()
    [again] = await finder.find([_part("U7", "MCP73031", datasheet=url, lcsc="C424093")])
    assert fetcher.calls == []
    assert (again.ref, again.status, again.source, again.path) == (
        "U7",
        "found",
        "schematic",
        status.path,
    )


async def test_falls_back_to_lcsc_when_the_url_gives_no_pdf(tmp_path):
    url = "https://example.com/product-page"
    pdf_url = "https://datasheet.lcsc.com/x.pdf"
    fetcher = FakeFetcher({url: b"<html>", **dict([_lcsc("C23922", pdf_url)]), pdf_url: PDF})
    [status] = await Datasheets(fetcher, DatasheetStore(tmp_path)).find(
        [_part("U16", "STM32F030C8Tx", datasheet=url, lcsc="C23922")]
    )
    assert (status.status, status.source, status.url) == ("found", "lcsc", pdf_url)


async def test_not_found_says_what_was_tried(tmp_path):
    fetcher = FakeFetcher(dict([_lcsc("C1", None)]))
    finder = Datasheets(fetcher, DatasheetStore(tmp_path))
    lcsc, url_404, mpn_only = await finder.find(
        [
            _part("U1", "X1", lcsc="C1"),
            _part("U2", "X2", datasheet="https://example.com/gone.pdf"),
            _part("U3", "ICM-42670-P", mpn="ICM-42670-P"),
        ]
    )
    assert (lcsc.status, lcsc.reason) == ("not_found", "LCSC lists no PDF for C1")
    assert url_404.reason == "the schematic's URL gave no PDF; no LCSC number to look up"
    assert (mpn_only.reason, mpn_only.part_number) == ("no LCSC number to look up", "ICM-42670-P")
    assert list(tmp_path.iterdir()) == []


async def test_generic_values_are_skipped_named_parts_are_not_found(tmp_path):
    fetcher = FakeFetcher({})
    generic, board, named = await Datasheets(fetcher, DatasheetStore(tmp_path)).find(
        [_part("U17", "~"), _part("U4", "Rust_Board"), _part("U1", "RP2040")]
    )
    assert (generic.status, generic.reason) == ("skipped", "generic value")
    assert board.status == "skipped"
    assert (named.status, named.reason) == ("not_found", "no datasheet URL or part number")
    assert fetcher.calls == []


async def test_parts_with_the_same_number_share_one_lookup(tmp_path):
    pdf_url = "https://datasheet.lcsc.com/ldo.pdf"
    fetcher = FakeFetcher({**dict([_lcsc("C47773", pdf_url)]), pdf_url: PDF})
    statuses = await Datasheets(fetcher, DatasheetStore(tmp_path)).find(
        [_part("U3", "RT9013", lcsc="C47773"), _part("U4", "RT9013", lcsc="C47773")]
    )
    assert [(s.ref, s.status) for s in statuses] == [("U3", "found"), ("U4", "found")]
    assert len(fetcher.calls) == 2  # LCSC's record and the PDF, once


async def test_a_slow_part_is_not_found_after_its_budget(tmp_path):
    class Slow:
        async def get(self, url: str) -> Fetched | None:
            await asyncio.sleep(5)
            return None

    [status] = await Datasheets(Slow(), DatasheetStore(tmp_path), part_timeout_s=0.05).find(
        [_part("U1", "X1", lcsc="C1")]
    )
    assert (status.status, status.reason) == ("not_found", "timed out after 0.05 s")


async def test_store_ignores_a_pdf_without_its_record(tmp_path):
    (tmp_path / "lcsc-C1.pdf").write_bytes(PDF)
    assert DatasheetStore(tmp_path).get("lcsc-C1") is None


# --- HttpFetcher -------------------------------------------------------------


def _fetcher(handler, addresses: dict[str, str], max_bytes: int = 1000) -> HttpFetcher:
    async def resolve(host: str, port: int) -> list[str]:
        return [addresses[host]]

    return HttpFetcher(
        timeout_s=1, max_bytes=max_bytes, resolve=resolve, transport=httpx.MockTransport(handler)
    )


PUBLIC = {"example.com": "93.184.215.14", "cdn.example.com": "93.184.215.15"}


async def test_fetcher_refuses_private_addresses_without_connecting():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=PDF)

    fetcher = _fetcher(handler, {"redis": "172.18.0.2", "localhost": "127.0.0.1"})
    assert await fetcher.get("http://redis:6379/") is None
    assert await fetcher.get("http://localhost/x.pdf") is None
    assert await fetcher.get("file:///etc/passwd") is None
    assert seen == []


async def test_fetcher_follows_redirects_but_checks_each_hop():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/to-cdn":
            return httpx.Response(302, headers={"location": "https://cdn.example.com/ds.pdf"})
        if request.url.path == "/to-lan":
            return httpx.Response(302, headers={"location": "http://gateway/admin"})
        return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})

    fetcher = _fetcher(handler, {**PUBLIC, "gateway": "192.168.10.19"})
    got = await fetcher.get("https://example.com/to-cdn")
    assert got is not None
    assert (got.url, got.body) == ("https://cdn.example.com/ds.pdf", PDF)
    assert await fetcher.get("https://example.com/to-lan") is None


async def test_fetcher_gives_none_for_errors_and_oversized_bodies():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * 2000)
        return httpx.Response(404)

    fetcher = _fetcher(handler, PUBLIC)
    assert await fetcher.get("https://example.com/missing") is None
    assert await fetcher.get("https://example.com/big") is None


def test_user_agent_is_the_app_name_in_ascii(monkeypatch):
    monkeypatch.setattr(settings, "PROJECT_NAME", "Häxrök")
    assert datasheet.user_agent() == "Haxrok-datasheets/1.0"


@pytest.mark.parametrize("name", ["", "日本語"])
def test_user_agent_falls_back_when_nothing_is_ascii(monkeypatch, name):
    monkeypatch.setattr(settings, "PROJECT_NAME", name)
    assert datasheet.user_agent() == "app-datasheets/1.0"
