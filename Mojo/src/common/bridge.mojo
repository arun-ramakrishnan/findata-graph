"""
Shared Python-bridge helpers (consolidation: single source of truth).

Canonical home for the `sys_exit` + `contains` / `join_list` / `py_str` /
`to_i` copies previously hand-propagated across db_access_probe,
graph_algos_probe, and common/integrity_check (self-acknowledged copies —
see the old "each probe stays self-contained" comment). Shared bodies are
byte-identical (`py_str` / `to_i` order canonicalized here).

Flat import (Makefile.mojo passes -I per package dir):
  from bridge import sys_exit, contains, join_list, py_str, to_i
"""

from std.python import Python, PythonObject


def sys_exit(code: Int) raises:
    # sys.exit raises SystemExit, which the bridge surfaces as an
    # unhandled error — os._exit terminates cleanly (integrity pattern)
    if code != 0:
        Python.evaluate("__import__('os')._exit(" + String(code) + ")")


def contains(lst: List[String], s: String) -> Bool:
    for i in range(len(lst)):
        if lst[i] == s:
            return True
    return False


def join_list(lst: List[String], sep: String) -> String:
    var out = String("")
    for i in range(len(lst)):
        if i > 0:
            out += sep
        out += lst[i]
    return out


def py_str(o: PythonObject) raises -> String:
    return String(o.__str__())


def to_i(o: PythonObject) raises -> Int:
    return Int(String(o.__str__()))


def main() raises:
    # Smoke: every src/<pkg>/*.mojo file must carry a main() or
    # `mojo build` refuses it ("module does not contain a 'main'
    # function") — the Makefile builds ALL of src/ into Mojo/bin/.
    var lst = List[String]()
    lst.append("a")
    lst.append("b")
    var n = to_i(Python.evaluate("40 + 2"))
    var s = py_str(Python.evaluate("'ok'"))
    print("bridge smoke:", contains(lst, "b"), join_list(lst, ","), n, s)
    sys_exit(0)
