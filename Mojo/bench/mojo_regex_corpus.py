"""Whole-corpus regex scan — Mojo-bridge vs native-Python comparison.

Uses the SAME pattern set as the interop battery (every findall-mode case
in mojo_regex_cases.json — single source), applied to every findata note
BODY (not just frontmatter). The Mojo probe (mojo_regex_corpus_scan.mojo)
mirrors scan_python() through the bridge and both sides must report the
same total match count (parity = the correctness check).
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import time

import regex as re

_HERE = pathlib.Path(__file__).parent
CASES_PATH = _HERE / "mojo_regex_cases.json"


def _paths_file():
    """Scratch corpus list — $TMPDIR/note_paths.txt (house rule, never /tmp)."""
    return pathlib.Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / "note_paths.txt"


def patterns():
    """[(pattern, flags_int)] for every findall-mode battery case."""
    with CASES_PATH.open() as fh:
        cases = json.load(fh)["cases"]
    out = []
    for c in cases:
        if c["mode"] != "findall":
            continue
        flags = 0
        for f in c.get("flags", []):
            flags |= getattr(re, f)
        out.append((c["pattern"], flags))
    return out


def npatterns():
    return len(patterns())


def read_doc(path):
    """Note body WITHOUT frontmatter (split on --- like the yaml tools).

    Everything after the closing `---` is the body; later `---` fences
    inside the body are content, not delimiters.
    """
    text = pathlib.Path(path).read_text(errors="replace")
    parts = text.split("---", 2)
    return parts[2] if len(parts) >= 3 else text


def scan_python_stats(paths_file=None, limit=0):
    paths_file = paths_file or str(_paths_file())
    """Native scan: every pattern over every doc body; stats dict.

    The Mojo probe reads this dict through the bridge for the parity
    check (matches) and the timing comparison (elapsed).
    """
    paths = [p for p in pathlib.Path(paths_file).read_text().splitlines() if p]
    if limit:
        paths = paths[:limit]
    pats = [re.compile(p, f) for p, f in patterns()]
    t0 = time.perf_counter()
    nbytes = 0
    matches = 0
    for path in paths:
        text = read_doc(path)
        nbytes += len(text)
        for pat in pats:
            matches += len(pat.findall(text))
    dt = time.perf_counter() - t0
    return {
        "docs": len(paths),
        "bytes": nbytes,
        "patterns": len(pats),
        "matches": matches,
        "elapsed": dt,
    }


def scan_python(paths_file=None, limit=0):
    paths_file = paths_file or str(_paths_file())
    s = scan_python_stats(paths_file, limit)
    return (
        f"python : docs={s['docs']} bytes={s['bytes']} "
        f"patterns={s['patterns']} matches={s['matches']} "
        f"elapsed={s['elapsed']:.3f}s"
    )
