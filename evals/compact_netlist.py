"""Convert a KiCad s-expression netlist (.net) into a compact text form for an LLM.

Output:
    # Components
    U2  TPS63070  (Regulator_Switching:TPS63070)
    ...
    # Nets
    +3V3: U2.7(VOUT) U2.8(VOUT) U2.9(L2) L1.2 C3.1 ...

Usage: python3 -I compact_netlist.py input.net > output.txt
"""

import re
import sys

TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')


def parse(text):
    stack = [[]]
    for tok in TOKEN.findall(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            node = stack.pop()
            stack[-1].append(node)
        else:
            if tok.startswith('"'):
                tok = tok[1:-1].replace('\\"', '"')
            stack[-1].append(tok)
    return stack[0][0]


def children(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def field(node, name, default=""):
    found = children(node, name)
    return found[0][1] if found and len(found[0]) > 1 else default


def main(path):
    with open(path, encoding="utf-8") as f:
        root = parse(f.read())

    print("# Components")
    for comps in children(root, "components"):
        for comp in children(comps, "comp"):
            ref = field(comp, "ref")
            value = field(comp, "value")
            libsource = children(comp, "libsource")
            part = ""
            if libsource:
                part = f"{field(libsource[0], 'lib')}:{field(libsource[0], 'part')}"
            print(f"{ref}  {value}  ({part})")

    print("\n# Nets")
    for nets in children(root, "nets"):
        for net in children(nets, "net"):
            name = field(net, "name")
            pins = []
            for node in children(net, "node"):
                ref, pin = field(node, "ref"), field(node, "pin")
                func = field(node, "pinfunction")
                pins.append(f"{ref}.{pin}({func})" if func else f"{ref}.{pin}")
            print(f"{name}: {' '.join(pins)}")


if __name__ == "__main__":
    main(sys.argv[1])
