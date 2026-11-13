"""
 Corpus sweep — the one Mojo tool over the whole findata corpus.

 Phases (argv[1], default "all"):
   yaml   — vendored mojo-yaml (Mojo/vendor/mojo-yaml) parses every
            note FRONTMATTER; expects FAIL: 0. PURE MOJO: no Python
            bridge, no CPython — the vendoring proof stays hermetic.
   regex  — every findall battery pattern (Mojo/bench/mojo_regex_cases
            .json, single source) over every note BODY, driven through
            the Python `regex` bridge; match-count PARITY with the
            native Python scan (Mojo/bench/mojo_regex_corpus.py) is the
            correctness check, the two elapsed times are the comparison.
 Shared: $TMPDIR/note_paths.txt (one absolute path per line). Regenerate:
   .venv/bin/python3 -c "import sys; sys.path.insert(0,'.'); \
     from helpers.maintenance.rebuild_note_search import _iter_findata_docs; \
     print('\\n'.join(str(p) for _t, p, _r in _iter_findata_docs()))" \
     > "$TMPDIR/note_paths.txt"
 (the bench harness regenerates it before each corpus leg)

 Standing runs:
   make mojo-bench MOJO_BENCH_ARGS='--leg yaml-corpus'    # phase yaml
   make mojo-bench MOJO_BENCH_ARGS='--leg regex-corpus'   # phase regex
 The native phase is AD-HOC ONLY (~60 s; removed from the harness as too
 slow — measured 2026-08-29: 8.5x slower than Python and 18/24 pattern
 mismatches, see bench_report.txt history):
   Mojo/bin/corpus_sweep native
"""


from std.collections import Dict
from std.python import Python, PythonObject
from std.sys import argv
from std.time import perf_counter_ns

from yaml import parse
from bridge import py_str


def scratch_paths_file() raises -> String:
    """$TMPDIR/note_paths.txt (house rule, never /tmp).

    Bridge fallback only: the standing harness passes the paths file as
    argv[2] so the yaml phase stays PURE MOJO (no CPython init); the
    bridge resolution serves ad-hoc runs that omit it.
    """
    var tmp = py_str(
        Python.evaluate("__import__('os').environ.get('TMPDIR', '')")
    )
    if tmp.byte_length() == 0:
        tmp = py_str(Python.evaluate("__import__('tempfile').gettempdir()"))
    return tmp + "/note_paths.txt"


def load_paths(paths_file: String = "") raises -> List[String]:
    var target = paths_file
    if target.byte_length() == 0:
        target = scratch_paths_file()
    var pf = open(target, "r")
    var raw = pf.read()
    pf.close()
    var out = List[String]()
    for span in raw.split("\n"):
        if span.byte_length() > 0:
            out.append(String(span))
    return out.copy()  # List is not ImplicitlyCopyable


def split_doc(body: String) raises -> Tuple[String, String]:
    """(frontmatter, body) split on --- like every other corpus tool.

    Everything after the closing `---` is the body; later `---` fences
    inside the body are content, not delimiters.
    """
    var parts = body.split("---", 2)
    if len(parts) >= 3:
        return (String(parts[1]), String(parts[2]))
    return ("", body)


def phase_yaml(paths: List[String]) raises -> Bool:
    var ok = 0
    var fail = 0
    var shown = 0
    for path in paths:
        var f = open(path, "r")
        var body = f.read()
        f.close()
        var fm = split_doc(body)[0]
        if fm.byte_length() == 0:
            fail += 1
            if shown < 25:
                shown += 1
                print("FAILED: no frontmatter |", path)
            continue
        try:
            _ = parse(fm)
            ok += 1
        except e:
            fail += 1
            if shown < 25:
                shown += 1
                print("FAILED:", String(e), "|", path)
    print("yaml: OK:", ok, " FAIL:", fail)
    return fail == 0


def phase_regex(paths: List[String], paths_file: String) raises -> Bool:
    var regex = Python.import_module("regex")
    Python.evaluate("__import__('sys').path.insert(0, 'Mojo/bench')")
    var corpus = Python.import_module("mojo_regex_corpus")

    # patterns compiled through the bridge (same objects a Python tool
    # would hold — fair comparison against scan_python_stats)
    var npat = Int(String(corpus.npatterns().__str__()))
    var pats = corpus.patterns()
    var compiled = Dict[Int, PythonObject]()
    for i in range(npat):
        compiled[i] = regex.compile(pats[i][0], pats[i][1])

    var t0 = perf_counter_ns()
    var nbytes = 0
    var matches = 0
    for path in paths:
        var f = open(path, "r")
        var body = f.read()
        f.close()
        var text = split_doc(body)[1]
        nbytes += text.byte_length()
        for p in range(npat):
            var res = compiled[p].findall(text)
            matches += res.__len__()  # dunder returns Mojo Int directly
    var t1 = perf_counter_ns()
    var dt = Float64(t1 - t0) / 1_000_000_000.0
    print(
        "mojo  : docs=",
        len(paths),
        " bytes=",
        nbytes,
        " patterns=",
        npat,
        " matches=",
        matches,
        " elapsed=",
        dt,
        "s",
    )

    # python native side + parity verdict (same paths file as the mojo
    # side — argv[2] must reach BOTH sides or parity compares different
    # corpora)
    var py_line = corpus.scan_python(paths_file)
    print(py_line)
    var st = corpus.scan_python_stats(paths_file)
    var py_matches = Int(String(st["matches"].__str__()))
    var py_dt = Float64(String(st["elapsed"].__str__()))
    if matches == py_matches:
        print(
            "PARITY OK: ",
            matches,
            "matches on both sides; mojo/python time ratio = ",
            dt / py_dt,
        )
        return True
    print("PARITY FAIL: mojo=", matches, " python=", py_matches)
    return False


def main() raises:
    var args = argv()
    var phase = "all"
    if len(args) >= 2:
        phase = String(args[1])
    if phase != "all" and phase != "yaml" and phase != "regex":
        print("usage: corpus_sweep [all|yaml|regex]")
        raise Error("unknown phase " + phase)
    var paths_file = ""
    if len(args) >= 3:
        paths_file = String(args[2])
    var paths = load_paths(paths_file)
    var ok = True
    if phase == "all" or phase == "yaml":
        ok = phase_yaml(paths) and ok
    if phase == "all" or phase == "regex":
        ok = phase_regex(paths, paths_file) and ok
    if not ok:
        raise Error("corpus sweep failed")
