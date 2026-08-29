import json
import pathlib

import regex as re

CASES_PATH = pathlib.Path(__file__).with_name("mojo_regex_cases.json")


def run_case(c):  # noqa: C901
    """Dispatch ONE case dict through the regex module; return the got value.

    Modes: findall, finditer_groups, search_groupdict, search_group,
    match_bool, fullmatch_bool, split, sub, subn. Flags arrive as regex
    attribute names ("IGNORECASE", "MULTILINE", ...). Both sides (native
    CPython and Mojo-via-bridge) execute the identical dispatch.
    """
    mode = c["mode"]
    pat = c["pattern"]
    s = c["input"]
    flags = 0
    for f in c.get("flags", []):
        flags |= getattr(re, f)
    if mode == "findall":
        return re.findall(pat, s, flags)
    if mode == "finditer_groups":
        # lists (not tuples): expected values round-trip through JSON as
        # lists, and ('a',) != ['a'] in Python.
        return [list(m.groups()) for m in re.finditer(pat, s, flags)]
    if mode == "search_groupdict":
        m = re.search(pat, s, flags)
        return dict(m.groupdict()) if m else None
    if mode == "search_group":
        m = re.search(pat, s, flags)
        return m.group(c["group"]) if m else None
    if mode == "match_bool":
        return re.match(pat, s, flags) is not None
    if mode == "fullmatch_bool":
        return re.fullmatch(pat, s, flags) is not None
    if mode == "split":
        return re.split(pat, s, flags)
    if mode == "sub":
        return re.sub(pat, c["repl"], s, count=c.get("count", 0), flags=flags)
    if mode == "subn":
        txt, n = re.subn(pat, c["repl"], s, count=c.get("count", 0), flags=flags)
        return [txt, n]
    raise ValueError(f"unknown mode {mode!r} in case {c.get('name')!r}")


def _load():
    with CASES_PATH.open() as fh:
        return json.load(fh)["cases"]


def cases():
    """The battery as data: (name, callable, expected) per case.

    The JSON is the SINGLE source fed to both sides — callables run
    natively in CPython (python_results/run) and from Mojo through the
    bridge (mojo_regex_probe.mojo), which compares the two.
    """
    return [(c["name"], lambda c=c: run_case(c), c["expected"]) for c in _load()]


def ncases():
    return len(_load())


def python_results():
    """name -> (repr(got), repr(exp)) computed NATIVELY in CPython."""
    out = {}
    for name, fn, exp in cases():
        out[name] = (repr(fn()), repr(exp))
    return out


def run():
    """Native self-check (CPython side alone): PASS/FAIL vs expected."""
    res = []

    def chk(name, got, exp):
        ok = got == exp
        res.append((("PASS" if ok else "FAIL"), name, got, exp))
        return ok

    for name, fn, exp in cases():
        chk(name, fn(), exp)
    npass = sum(1 for r in res if r[0] == "PASS")
    lines = [f"{s} {n}: got={g!r}" + ("" if s == "PASS" else f" exp={e!r}") for s, n, g, e in res]
    lines.append(f"--- {npass}/{len(res)} checks passed")
    return "\n".join(lines)


def run_cases(iters=100000):
    """`iters` case calls, cycling all case callables; return elapsed seconds."""
    import time
    cs = cases()
    nc = len(cs)
    t0 = time.perf_counter()
    for i in range(iters):
        cs[i % nc][1]()
    return time.perf_counter() - t0


def bench_report(iters=100000):
    t = run_cases(iters)
    return f"{iters} case calls: {t:.4f} s, {iters/t:.0f} calls/s"
