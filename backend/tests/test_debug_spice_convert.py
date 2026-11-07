"""Converting each found SPICE model to run in ngspice and checking that it
loads: the deck, the pins, the safety refusals and the verdict on canned
logs; then PSpice and LTspice constructs in the real ngspice, skipped when
it isn't installed. Vendor models aren't committed; ``make spice-corpus``
runs real ones."""

import shutil
from pathlib import Path

import pytest

from app.features.debug.schemas import SpiceModelStatus
from app.features.debug.spice import Log, Ngspice
from app.features.debug.spice_convert import (
    check,
    deck,
    missing_param,
    reached,
    spiceinit,
    subckt_pins,
    unsafe,
    verdict,
)

LOADED = "Circuit: * load check\nDoing analysis at TEMP = 27.000000\nNo. of Data Rows : 1\n"
SOLVED = Log(0, LOADED)


class FakeLoader:
    """Answers each load with ``log``; records the deck and files it got."""

    def __init__(self, log: Log | None = SOLVED) -> None:
        self.log = log
        self.calls: list[tuple[str, dict[str, bytes]]] = []

    async def load(self, netlist: str, files: dict[str, bytes]) -> Log | None:
        self.calls.append((netlist, files))
        return self.log


def _kept(
    tmp_path: Path, text: bytes, status: str = "pspice", subckt: str = "M"
) -> SpiceModelStatus:
    folder = tmp_path / "TPS1"
    folder.mkdir(exist_ok=True)
    (folder / "m.lib").write_bytes(text)
    (folder / "record.json").write_text("{}")
    found = SpiceModelStatus(
        ref="U1",
        value="TPS1",
        status="pspice",
        source="web",
        file="m.lib",
        subckt=subckt,
        path=str(folder / "m.lib"),
    )
    return found.model_copy(update={"status": status})


# --- the deck ---------------------------------------------------------------------


def test_subckt_pins_follow_continuations_and_stop_at_params():
    text = b"""* x
.SUBCKT TPS63802_TRANS VIN VOUT
+ EN GND PARAMS: FSW=2.5meg
R1 VIN 0 1
.ENDS
.subckt other a b c
.ends
"""
    assert subckt_pins(text, "tps63802_trans") == ["VIN", "VOUT", "EN", "GND"]
    assert subckt_pins(b".subckt amp in out gain=10\n.ends\n", "amp") == ["in", "out"]
    assert subckt_pins(text, "missing") is None


def test_deck_grounds_each_pin_through_a_megohm_and_runs_an_op():
    text = deck("lib/m.lib", "AMP", ["IN", "OUT"])
    assert text.splitlines() == [
        "* load check: AMP",
        '.include "lib/m.lib"',
        "X1 p1 p2 AMP",
        "R1 p1 0 1meg",
        "R2 p2 0 1meg",
        ".op",
        ".end",
    ]


def test_spiceinit_sets_the_dialect_mode():
    assert spiceinit("pspice").startswith("set ngbehavior=ps\n")
    assert spiceinit("ltspice").startswith("set ngbehavior=lt\n")
    assert "ngbehavior" not in spiceinit("ngspice")
    assert "optran" in spiceinit("ngspice")


# --- what isn't run ---------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        ".control\nshell cat /etc/passwd\n.endc",
        "  .ENDC",
        ".include /etc/passwd",
        '.inc "~/.ssh/id_rsa"',
        ".lib ../../secrets.lib",
        ".include C:\\models\\x.lib",
        ".include \\\\server\\share\\x.lib",
    ],
)
def test_unsafe_refuses_control_blocks_and_includes_outside_the_folder(line):
    assert unsafe({"m.lib": f".subckt M a b\n{line}\n.ends\n".encode()}) is not None


def test_unsafe_lets_includes_inside_the_folder_through():
    files = {"m.lib": b'.include "sub/x.lib"\n.lib TT\n.subckt M a b\n.ends\n'}
    assert unsafe(files) is None


def test_reached_follows_includes_from_the_picked_file_only():
    files = {
        "m.lib": b'.include "sub/a.lib"\n.subckt M a b\n.ends\n',
        "sub/a.lib": b".inc b.lib\n",
        "sub/b.lib": b"* b\n",
        "bench.cir": b".include ..\\SCHEMATIC1.net\n",
    }
    assert sorted(reached(files, "m.lib")) == ["m.lib", "sub/a.lib", "sub/b.lib"]


async def test_check_ignores_a_test_bench_the_model_doesnt_include(tmp_path):
    status = _kept(tmp_path, b".subckt M a b\nR1 a b 1\n.ends\n")
    (tmp_path / "TPS1" / "transient.cir").write_text(".include ..\\SCHEMATIC1.net\n")
    loader = FakeLoader()
    [got] = await check([status], {}, loader)
    assert got.loads is True
    assert sorted(loader.calls[0][1]) == [".spiceinit", "m.lib"]


async def test_check_refuses_an_unsafe_model_without_running_it(tmp_path):
    status = _kept(tmp_path, b".subckt M a b\n.ends\n.control\nshell id\n.endc\n")
    loader = FakeLoader()
    [got] = await check([status], {}, loader)
    assert got.loads is False and ".control" in got.reason
    assert loader.calls == []


async def test_check_leaves_out_uploaded_files_outside_the_project(tmp_path):
    status = SpiceModelStatus(
        ref="U1", value="X", status="ltspice", source="upload", file="lib/m.lib", subckt="M"
    )
    upload = {
        "lib/m.lib": b".subckt M a b\nR1 a b 1\n.ends\n",
        "../evil.lib": b"x",
        "/abs.lib": b"x",
        "board.kicad_sch": b"(kicad_sch)",
    }
    loader = FakeLoader()
    [got] = await check([status], upload, loader)
    assert got.loads is True
    netlist, files = loader.calls[0]
    assert sorted(files) == [".spiceinit", "lib/m.lib"]
    assert '.include "lib/m.lib"' in netlist
    assert files[".spiceinit"].startswith(b"set ngbehavior=lt\n")


# --- the verdict ------------------------------------------------------------------


def test_verdict_on_canned_logs():
    assert verdict(Log(0, LOADED)) == (True, "")
    no_op = "Doing analysis at TEMP = 27\nError: no convergence in transient op\n"
    assert verdict(Log(1, no_op)) == (True, "loads; no operating point with the pins grounded")
    assert verdict(Log(None, "Doing analysis at TEMP = 27\n"))[0] is True
    assert verdict(Log(None, "")) == (False, "ngspice ran out of time loading it")
    failed = """Note: Compatibility modes selected: lt
Circuit: * load check
Error on line 2 or its substitute:
  c1 a 0 1u rser=10m
unknown parameter (rser)
Simulation interrupted due to error!
ERROR: fatal error in ngspice, exit(1)
"""
    loads, reason = verdict(Log(1, failed))
    assert loads is False
    assert (
        reason == "Error on line 2 or its substitute: c1 a 0 1u rser=10m unknown parameter (rser)"
    )
    assert verdict(Log(1, "nothing useful\n")) == (False, "ngspice exited with 1")


async def test_check_sets_a_parameter_the_model_leaves_to_the_schematic(tmp_path):
    lib = b".SUBCKT M A B\nR1 A B {8.77Meg/((V_out/0.8)-1)}\n.ENDS\n"
    status = _kept(tmp_path, lib)
    undefined = Log(1, "Undefined parameter [v_out]\nExpression err: v_out/0.8)-1)}\n")

    class Loader(FakeLoader):
        async def load(self, netlist: str, files: dict[str, bytes]) -> Log | None:
            self.calls.append((netlist, files))
            return self.log if ".param v_out=1" in netlist else undefined

    loader = Loader()
    [got] = await check([status], {}, loader)
    assert got.loads is True
    assert got.reason == "needs v_out set by the schematic (1 here)"
    assert len(loader.calls) == 2


async def test_check_doesnt_define_a_name_ngspice_misread():
    files = {"m.lib": b"E1 a 0 LAPLACE {V(b)} = {1/(1+s)}\n"}
    assert missing_param(Log(1, "Undefined parameter [v]\n"), files) is None
    assert missing_param(Log(1, "Undefined parameter [vo]\n"), {"m.lib": b"R1 a b {VO}\n"}) == "vo"


# --- which statuses are checked ---------------------------------------------------


async def test_check_runs_each_found_subckt_once_and_skips_the_rest(tmp_path):
    lib = b".SUBCKT M A B\nR1 A B 1\n.ENDS\n.model D1 D(Is=1e-14)\n"
    found = _kept(tmp_path, lib)
    twin = found.model_copy(update={"ref": "U2"})
    card = found.model_copy(update={"ref": "U3", "subckt": "D1"})
    encrypted = SpiceModelStatus(ref="U4", value="X", status="encrypted", file="x.lib")
    none = SpiceModelStatus(ref="U5", value="Y", status="none", reason="no model")
    loader = FakeLoader()
    got = await check([found, twin, card, encrypted, none], {}, loader)
    assert [g.loads for g in got] == [True, True, None, None, None]
    assert got[2].reason == "a .model card; only subcircuits are load-checked"
    assert got[4].reason == "no model"
    [(netlist, files)] = loader.calls
    assert sorted(files) == [".spiceinit", "m.lib"]
    assert "X1 p1 p2 M" in netlist


async def test_check_says_nothing_when_ngspice_is_missing(tmp_path):
    status = _kept(tmp_path, b".subckt M a b\n.ends\n").model_copy(update={"reason": "picked"})
    [got] = await check([status], {}, FakeLoader(None))
    assert got.loads is None and got.reason == "picked"


async def test_check_keeps_the_finders_note_beside_ngspices_error(tmp_path):
    status = _kept(tmp_path, b".subckt M a b\n.ends\n").model_copy(update={"reason": "picked"})
    [got] = await check([status], {}, FakeLoader(Log(1, "Error: unknown subckt: x1 m\n")))
    assert got.loads is False and got.reason == "picked; Error: unknown subckt: x1 m"


# --- Ngspice.load -----------------------------------------------------------------


def _script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "ngspice"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


async def test_ngspice_load_writes_the_files_and_keeps_the_log(tmp_path):
    ok = _script(
        tmp_path,
        'test "$*" = "-b -o load.log load.cir" && test -f .spiceinit && test -f sub/m.lib'
        " && echo 'Doing analysis' > load.log && echo stdout\n",
    )
    got = await Ngspice(binary=ok).load("* deck\n", {".spiceinit": b"", "sub/m.lib": b""})
    assert got == Log(0, "Doing analysis\nstdout\n")
    slow = _script(tmp_path, "echo 'Doing analysis' > load.log; sleep 5\n")
    assert await Ngspice(binary=slow, timeout_s=0.2).load("* deck\n", {}) == Log(
        None, "Doing analysis\n"
    )
    assert await Ngspice(binary=str(tmp_path / "missing")).load("* deck\n", {}) is None
    with pytest.raises(ValueError):
        await Ngspice(binary=ok).load("* deck\n", {"../out.lib": b""})


# --- the real ngspice -------------------------------------------------------------

needs_ngspice = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

# Constructs from vendor models, each in a two-pin subcircuit ``M``. The
# ones ngspice's compatibility modes don't read are kept as failures, so a
# newer ngspice that reads them shows up here; a rewrite is added only when
# a real model needs one (plan 004 step 5).
LOADS = {
    "pspice VALUE": ("pspice", "E1 a 0 VALUE={ V(b)*2 + 1 }\nR1 b 0 1k"),
    "pspice PARAMS": ("pspice", "E1 a 0 VALUE={ V(b)*{G} }\nR1 b 0 1k", "PARAMS: G=2"),
    "pspice IF": ("pspice", "E1 a 0 VALUE={ IF(V(b) > 0.5, 1, 0) }\nR1 b 0 1k"),
    "pspice TABLE": ("pspice", "E1 a 0 TABLE {V(b)} = (0,0) (1,2) (2,3)\nR1 b 0 1k"),
    "pspice STP": ("pspice", "E1 a 0 VALUE={ STP(V(b)) }\nR1 b 0 1k"),
    "pspice G VALUE": ("pspice", "G1 a 0 VALUE={ V(b)/1k }\nR1 b 0 1k\nR2 a 0 1k"),
    "pspice VSWITCH": (
        "pspice",
        "S1 a 0 b 0 SW1\nR1 b 0 1k\n.model SW1 VSWITCH (RON=1 ROFF=1meg VON=1 VOFF=0)",
    ),
    "ltspice uplim": ("ltspice", "B1 a 0 V=uplim(V(b), 1, 0.1)\nR1 b 0 1k"),
    "ltspice params": ("ltspice", "B1 a 0 V=V(b)*G\nR1 b 0 1k", "params: G=2"),
    "ltspice SW": (
        "ltspice",
        "S1 a 0 b 0 SWL\nR1 b 0 1k\n.model SWL SW(Ron=1 Roff=1meg Vt=0.5 Vh=0.1)",
    ),
    "ltspice diode": ("ltspice", "D1 a b DL\n.model DL D(Is=1e-14 Rs=0.1 Vfwd=0.7 Ron=0.1)"),
}
FAILS = {
    "pspice LAPLACE": ("pspice", "E1 a 0 LAPLACE {V(b)} = {1/(1+s*1u)}\nR1 b 0 1k"),
    "ltspice Rser": ("ltspice", "C1 a 0 1u Rser=10m\nL1 a b 1u Rser=1m"),
    "ltspice if()": ("ltspice", "B1 a 0 V=if(V(b) > 0.5, 1, 0)\nR1 b 0 1k"),
}


def _snippet(tmp_path: Path, case: tuple[str, ...]) -> SpiceModelStatus:
    status, body, *params = case
    head = f".subckt M a b {params[0]}" if params else ".subckt M a b"
    return _kept(tmp_path, f"{head}\n{body}\n.ends\n".encode(), status)


@needs_ngspice
@pytest.mark.parametrize("name", LOADS)
async def test_ngspice_loads_vendor_construct(tmp_path, name):
    [got] = await check([_snippet(tmp_path, LOADS[name])], {}, Ngspice())
    assert got.loads is True, got.reason


@needs_ngspice
@pytest.mark.parametrize("name", FAILS)
async def test_ngspice_refuses_vendor_construct_with_its_error(tmp_path, name):
    [got] = await check([_snippet(tmp_path, FAILS[name])], {}, Ngspice())
    assert got.loads is False
    assert got.reason and "exited with" not in got.reason, got.reason


@needs_ngspice
async def test_real_ngspice_refuses_a_control_block(tmp_path):
    canary = tmp_path / "ran"
    lib = f".subckt M a b\nR1 a b 1\n.ends\n.control\nshell touch {canary}\n.endc\n"
    [got] = await check([_kept(tmp_path, lib.encode(), "ngspice")], {}, Ngspice())
    assert got.loads is False and not canary.exists()
