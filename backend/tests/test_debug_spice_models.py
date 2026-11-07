"""Finding and classifying each IC's SPICE model: the classifier on sample
files of each class, picking the subcircuit, ZIPs, product-page links, the
uploaded ``Sim.Library`` file, and the kept copy. The network is a dict of
URLs."""

import io
import json
import zipfile

from app.features.debug.datasheet import LCSC_PRODUCT_URL, Fetched
from app.features.debug.design import Part, parse_netlist
from app.features.debug.schemas import DatasheetStatus
from app.features.debug.spice_models import (
    TI_PRODUCT_URL,
    ModelFile,
    SpiceModels,
    SpiceModelStore,
    classify,
    model_files,
    model_links,
    pick,
    ti_product_names,
    uploaded_library,
)

NGSPICE = b"""* LDO macro model
.subckt LDO33 IN OUT GND
R1 IN OUT 1
B1 OUT GND V=V(IN)>3.3 ? 3.3 : V(IN)
.ends LDO33
"""
PSPICE_NAMED = b"""*$
* TPS63802
* Simulator: PSPICE
.SUBCKT TPS63802_TRANS VIN VOUT GND
E1 1 0 VALUE {V(VIN)*2}
.ENDS
"""
PSPICE_SYNTAX = b""".SUBCKT AMP IN OUT PARAMS: GAIN=10
E1 OUT 0 LAPLACE {V(IN)} = {GAIN/(1+s)}
.ENDS
"""
LTSPICE_NAMED = b"""* Copyright Analog Devices. For use with LTspice only.
.subckt LT3080 IN OUT SET
.ends
"""
LTSPICE_SYNTAX = b""".subckt CAP 1 2
C1 1 2 10u Rser=5m
.ends
"""
PSPICE_ENCRYPTED = b""".SUBCKT SECRET 1 2
$CDNENCSTART
0a1b2c3d4e
$CDNENCFINISH
.ENDS
"""
LTSPICE_ENCRYPTED = b"* LTspice Encrypted File\n*\nBegin:\n7A 3F 22 91\n"
BINARY = bytes(range(256)) * 4


def test_classifier_on_a_sample_of_each_class():
    assert classify(NGSPICE) == "ngspice"
    assert classify(PSPICE_NAMED) == "pspice"
    assert classify(PSPICE_SYNTAX) == "pspice"
    assert classify(LTSPICE_NAMED) == "ltspice"
    assert classify(LTSPICE_SYNTAX) == "ltspice"
    assert classify(PSPICE_ENCRYPTED) == "encrypted"
    assert classify(LTSPICE_ENCRYPTED) == "encrypted"
    assert classify(BINARY) == "encrypted"


def test_simulator_words_in_code_lines_are_not_a_named_simulator():
    # Only comments name the simulator; a node called "pspice" isn't one.
    assert classify(b".subckt X pspice 0\nR1 pspice 0 1\n.ends\n") == "ngspice"


def test_pick_prefers_sim_name_then_the_mpn_then_the_first_subckt():
    files = [
        ModelFile("a.lib", b".model DX D\n.subckt OTHER 1 2\n.ends\n"),
        ModelFile("b.lib", b".subckt TPS63802_TRANS 1 2\n.ends\n.subckt AUX 1\n.ends\n"),
    ]
    assert pick(files, sim_name="aux") == (files[1], "AUX")
    assert pick(files, mpn="TPS63802DLAR") == (files[1], "TPS63802_TRANS")
    assert pick(files) == (files[0], "OTHER")
    assert pick([ModelFile("m.lib", b".model DX D\n")]) == (
        ModelFile("m.lib", b".model DX D\n"),
        "DX",
    )
    assert pick([ModelFile("e.lib", BINARY)]) == (ModelFile("e.lib", BINARY), None)
    assert pick([ModelFile("readme.lib", b"nothing here")]) is None


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_a_zip_keeps_only_its_model_text_files_by_basename():
    body = _zip(
        {
            "pkg/TPS63802_TRANS.LIB": PSPICE_NAMED,
            "pkg/TPS63802.OLB": b"binary symbol",
            "pkg/sim/TPS63802.DSN": b"design",
            "../../evil.lib": NGSPICE,
            "pkg/sub/": b"",
        }
    )
    got = model_files(Fetched("https://x.test/m.zip", "application/zip", body))
    assert got == [("TPS63802_TRANS.LIB", PSPICE_NAMED), ("evil.lib", NGSPICE)]


def test_a_zip_caps_member_count_and_size(monkeypatch):
    from app.features.debug import spice_models

    monkeypatch.setattr(spice_models, "_ZIP_MAX_FILES", 2)
    monkeypatch.setattr(spice_models, "_MODEL_MAX_BYTES", 100)
    body = _zip({"big.lib": b"*" * 200, "a.lib": NGSPICE[:50], "b.lib": b"x", "c.lib": b"y"})
    got = model_files(Fetched("https://x.test/m.zip", "application/zip", body))
    assert [n for n, _ in got] == ["a.lib", "b.lib"]


def test_a_plain_download_is_a_model_only_when_it_reads_as_one():
    assert model_files(Fetched("https://x.test/d/ldo.cir", "text/plain", NGSPICE)) == [
        ("ldo.cir", NGSPICE)
    ]
    page = b"<html><a href='x.lib'>model</a></html>"
    assert model_files(Fetched("https://x.test/page", "text/html", page)) == []
    assert model_files(Fetched("https://x.test/notes", "text/plain", b"hello")) == []


TI_PAGE = """<html><body>
<a href="/tool/PSPICE-FOR-TI">PSpice for TI design and simulation tool</a>
<a href="https://www.ti.com/lit/zip/SLVMCX0">TPS63802 Encrypted PSpice Model</a>
<a href="https://www.ti.com/lit/zip/SLVMCX1">TPS63802 Unencrypted PSpice Transient
  Model Package (Rev. C)</a>
<a href="/lit/zip/SLVM999">TPS63802 TINA-TI Spice Model</a>
<a href="/login?next=/lit/zip/x.zip">Sign in to download</a>
<a href="/docs/ibis.zip">TPS63802 IBIS model</a>
<a href="/files/ref.pdf">Datasheet</a>
</body></html>"""


def test_model_links_rank_an_unencrypted_model_of_this_part_first():
    links = model_links(TI_PAGE, "https://www.ti.com/product/TPS63802", "TPS63802DLAR")
    assert links[0] == "https://www.ti.com/lit/zip/SLVMCX1"
    # An encrypted model of the part still beats a simulator's download page.
    assert links == [
        "https://www.ti.com/lit/zip/SLVMCX1",
        "https://www.ti.com/lit/zip/SLVMCX0",
        "https://www.ti.com/tool/PSPICE-FOR-TI",
    ]


def test_ti_product_names_trim_the_orderable_suffix():
    part = Part("U1", "TPS63802DLAR", "", "")
    assert ti_product_names(part, "TPS63802DLAR", []) == ["TPS63802"]
    urls = ["https://www.ti.com/lit/ds/symlink/tlv755p.pdf"]
    assert ti_product_names(part, "TLV75533PDBVR", urls) == ["TLV755P", "TLV75533"]


def test_uploaded_library_resolves_kiprjmod_relative_paths_and_basenames():
    files = {
        "board.kicad_sch": b"",
        "spice/ldo.lib": NGSPICE,
        "sub/board2.kicad_sch": b"",
        "models/OPA.LIB": b"",
    }
    assert (
        uploaded_library("${KIPRJMOD}/spice/ldo.lib", files, "board.kicad_sch") == "spice/ldo.lib"
    )
    assert uploaded_library("spice/ldo.lib", files, "board.kicad_sch") == "spice/ldo.lib"
    assert uploaded_library(r"C:\Users\me\opa.lib", files, "board.kicad_sch") == "models/OPA.LIB"
    assert uploaded_library("missing.lib", files, "board.kicad_sch") is None


class FakeFetcher:
    """Serves bodies by URL; anything else fails like a 404."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    async def get(self, url: str) -> Fetched | None:
        self.calls.append(url)
        body = self.pages.get(url)
        return None if body is None else Fetched(url, "application/octet-stream", body)


def _lcsc(code: str, brand: str, mpn: str) -> tuple[str, bytes]:
    result = {"productCode": code, "brandNameEn": brand, "productModel": mpn}
    return LCSC_PRODUCT_URL.format(code=code), json.dumps({"code": 200, "result": result}).encode()


def _part(ref: str, value: str, **kw: str) -> Part:
    return Part(ref, value, "lib:part", "", **kw)


def _sheet(ref: str, url: str | None = None) -> DatasheetStatus:
    """A datasheet status: found from the schematic's URL when ``url`` is given."""
    if url:
        return DatasheetStatus(ref=ref, value="", status="found", source="schematic", url=url)
    return DatasheetStatus(ref=ref, value="", status="not_found")


async def test_ti_part_by_lcsc_brand_found_kept_and_read_back(tmp_path):
    zipped = _zip({"TPS63802_TRANS.LIB": PSPICE_NAMED, "TPS63802.OLB": b"x"})
    pages = dict(
        [
            _lcsc("C2845237", "TI", "TPS63802DLAR"),
            (TI_PRODUCT_URL.format(name="TPS63802"), TI_PAGE.encode()),
            ("https://www.ti.com/lit/zip/SLVMCX1", zipped),
        ]
    )
    fetcher = FakeFetcher(pages)
    finder = SpiceModels(fetcher, SpiceModelStore(tmp_path))
    part = _part("U15", "TPS63802DLAR", lcsc="C2845237")
    [status] = await finder.find([part], [_sheet("U15")], {}, "board.kicad_sch")
    assert (status.status, status.source, status.file, status.subckt) == (
        "pspice",
        "web",
        "TPS63802_TRANS.LIB",
        "TPS63802_TRANS",
    )
    assert status.url == "https://www.ti.com/lit/zip/SLVMCX1"
    assert status.part_number == "TPS63802DLAR"
    assert (tmp_path / "lcsc-C2845237" / "TPS63802_TRANS.LIB").read_bytes() == PSPICE_NAMED
    assert "path" not in status.model_dump()

    fetcher.calls.clear()
    [again] = await finder.find([part], [], {}, "board.kicad_sch")
    assert fetcher.calls == []
    assert (again.status, again.subckt, again.path) == ("pspice", "TPS63802_TRANS", status.path)


async def test_schematic_product_page_is_searched_only_when_it_gave_no_pdf(tmp_path):
    page_url = "https://vendor.test/p/LDO33"
    page = b'<a href="/m/ldo33.cir">LDO33 SPICE model</a>'
    fetcher = FakeFetcher({page_url: page, "https://vendor.test/m/ldo33.cir": NGSPICE})
    finder = SpiceModels(fetcher, SpiceModelStore(tmp_path))
    part = _part("U1", "LDO33", mpn="LDO33-A", datasheet=page_url)

    pdf_found = _sheet("U1", url=page_url)
    [none] = await finder.find([part], [pdf_found], {}, "b.kicad_sch")
    assert (none.status, fetcher.calls) == ("none", [])
    assert "no product page known" in none.reason

    [found] = await finder.find([part], [_sheet("U1")], {}, "b.kicad_sch")
    assert (found.status, found.file, found.subckt) == ("ngspice", "ldo33.cir", "LDO33")


async def test_sim_library_upload_is_used_before_the_web_and_never_kept(tmp_path):
    fetcher = FakeFetcher({})
    finder = SpiceModels(fetcher, SpiceModelStore(tmp_path))
    part = _part("U1", "LT3080", mpn="LT3080", sim_library="${KIPRJMOD}/lt.lib", sim_name="LT3080")
    files = {"b.kicad_sch": b"", "lt.lib": LTSPICE_NAMED}
    [status] = await finder.find([part], [], files, "b.kicad_sch")
    assert (status.status, status.source, status.file, status.subckt) == (
        "ltspice",
        "upload",
        "lt.lib",
        "LT3080",
    )
    assert (status.url, status.path, fetcher.calls) == (None, "", [])
    assert not any(tmp_path.iterdir())


async def test_sim_library_not_uploaded_says_so_and_tries_the_web(tmp_path):
    finder = SpiceModels(FakeFetcher({}), SpiceModelStore(tmp_path))
    part = _part("U1", "OPA333", mpn="OPA333", manufacturer="Acme", sim_library="opa.lib")
    [status] = await finder.find([part], [], {"b.kicad_sch": b""}, "b.kicad_sch")
    assert status.status == "none"
    assert status.reason.startswith("opa.lib not uploaded; no product page known for Acme")


async def test_generic_parts_are_skipped_and_misses_not_kept(tmp_path):
    fetcher = FakeFetcher({})
    finder = SpiceModels(fetcher, SpiceModelStore(tmp_path))
    parts = [_part("U1", "LDO"), _part("U2", "ESP32-C3", lcsc="C2838500")]
    skipped, none = await finder.find(parts, [], {}, "b.kicad_sch")
    assert (skipped.status, skipped.reason) == ("skipped", "generic value")
    assert none.status == "none"
    assert fetcher.calls == [LCSC_PRODUCT_URL.format(code="C2838500")]
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


async def test_same_part_twice_is_looked_up_once(tmp_path):
    fetcher = FakeFetcher({})
    finder = SpiceModels(fetcher, SpiceModelStore(tmp_path))
    parts = [_part("U1", "X123", lcsc="C1"), _part("U2", "X123", lcsc="C1")]
    a, b = await finder.find(parts, [], {}, "b.kicad_sch")
    assert (a.ref, b.ref) == ("U1", "U2")
    assert len(fetcher.calls) == 1


def test_parse_netlist_reads_sim_fields_and_kicad6_names():
    netlist = """(export (version "E")
      (components
        (comp (ref "U1") (value "LT3080")
          (fields (field (name "Sim.Library") "${KIPRJMOD}/lt.lib")
                  (field (name "Sim.Name") "LT3080"))
          (libsource (lib "x") (part "y") (description "")))
        (comp (ref "U2") (value "OPA333")
          (fields (field (name "Spice_Lib_File") "opa.lib") (field (name "Spice_Model") "OPA333"))
          (libsource (lib "x") (part "y") (description ""))))
      (nets))
    """
    parts = parse_netlist(netlist).parts
    assert (parts["U1"].sim_library, parts["U1"].sim_name) == ("${KIPRJMOD}/lt.lib", "LT3080")
    assert (parts["U2"].sim_library, parts["U2"].sim_name) == ("opa.lib", "OPA333")
