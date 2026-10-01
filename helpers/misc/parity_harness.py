#!/usr/bin/env python3
"""Byte-parity harness for refactors of functions with no cheap unit oracle.

c901_complexity_debt S1. Splitting a long function is only safe if the new
shape emits *the same bytes* as the old one, and a plain test suite will not
catch a reordered tally or a changed rounding. The protocol every extraction
in this house is expected to follow:

  1. render the function's output as text (never assert on objects -- repr
     order is not a contract);
  2. run it against the working tree AND against ``git show <ref>:<path>``;
  3. do both in a *fresh subprocess per side per seed*, with
     ``PYTHONHASHSEED`` pinned -- the seed must be set in the child
     environment, since the interpreter reads it at startup and setting it
     inside the process does nothing;
  4. byte-compare. A mismatch reverts the refactor; it is never "adjusted".

Two comparisons come out of one run, and they mean different things:

  PARITY       baseline vs working tree, same seed. A failure is a refactor
               regression. Reported as a WARNING and does not fail the run;
               pass --strict to gate on it.
  DETERMINISM  the same side across seeds. A failure is a pre-existing
               hash-order dependency in the *output*, not a refactor bug --
               see chain_tally_determinism -- so it is reported, not fatal.

Usage:
    parity_harness.py                 # every registered fixture
    parity_harness.py --list
    parity_harness.py bfs_path

Fixtures register themselves in ``REGISTRY``. Each needs a module path, the
repo-relative file to diff against, and a ``render(mod) -> str`` that works
against either side (it receives the module object, so nothing is imported by
name). Registry growth is the intended cost: each new split adds one entry.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SEEDS = (0, 1, 7)
DEFAULT_REF = "HEAD"


@dataclass(frozen=True)
class Fixture:
    """One refactor target under parity test."""

    name: str
    relpath: str
    render: Callable[[ModuleType], str]
    note: str = ""
    canary: bool = False
    inject_divergence: str = ""


# --- fixtures ------------------------------------------------------------- #


def _csr(n: int, edges: list[tuple[int, int]]) -> tuple[object, object]:
    """Undirected CSR for a node count + edge list (sorted, so seed-proof)."""
    import numpy as np

    adj: list[set[int]] = [set() for _ in range(n)]
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    offsets = np.zeros(n + 1, dtype=np.int32)
    for i in range(n):
        offsets[i + 1] = offsets[i] + len(adj[i])
    neighbors = np.fromiter((j for i in range(n) for j in sorted(adj[i])), dtype=np.int32)
    return offsets, neighbors


_BFS_GRAPHS: list[tuple[int, list[tuple[int, int]]]] = [
    (1, []),
    (2, [(0, 1)]),
    (3, [(0, 1), (1, 2)]),
    (4, [(0, 1), (1, 2), (2, 3)]),
    (4, [(0, 1), (0, 2), (0, 3)]),
    (5, [(0, 1), (1, 2), (2, 3), (3, 4), (4, 0)]),
    (5, [(0, 1), (2, 3)]),
    (6, [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0)]),
    (7, [(0, 1), (0, 2), (0, 3), (4, 5), (5, 6)]),
]


def _render_bfs_path(mod: ModuleType) -> str:
    """Every (src, dst, hop-cap) in a fixed graph set -- the Mojo oracle shape."""
    rows = []
    for n, edges in _BFS_GRAPHS:
        offsets, neighbors = _csr(n, edges)
        for src in range(n):
            for dst in range(n):
                for cap in (None, 0, 1, 2, 3, 99):
                    got = mod.bfs_path(offsets, neighbors, src, dst, cap)
                    rows.append(f"n={n} edges={edges} {src}->{dst} cap={cap} => {got}")
    return "\n".join(rows)


def _render_extract_relations(mod: ModuleType) -> str:
    """Fixed newsletter blocks through the full extraction pipeline."""
    from helpers.graph.fixture_graph_db import RELATION_CONTENTS, RELATION_KNOWN_NAMES

    resolver = mod.EntityResolver(list(RELATION_KNOWN_NAMES))
    rows: list[str] = []
    for i, content in enumerate(RELATION_CONTENTS):
        by_type, unresolved = mod.extract_relations(
            content,
            edition_title=f"Edition {i}",
            newsletter_type="The_Chatter",
            resolver=resolver,
        )
        rows.append(f"--- content {i}")
        for et in sorted(by_type):
            for e in sorted(by_type[et], key=lambda e: (e.source, e.target)):
                props = ", ".join(f"{k}={v!r}" for k, v in sorted(e.properties.items()))
                rows.append(f"{et}: {e.source} -> {e.target} props({props})")
        for u in sorted(unresolved, key=lambda u: (u.edge_type, u.target_mention)):
            rows.append(f"UNRESOLVED {u.edge_type}: {u.target_mention!r}")
    return "\n".join(rows)


def _render_resolve_ladder(mod: ModuleType) -> str:
    """Fixed (name, resolver_map) cases through the S2 tier ladder."""
    from helpers.graph.fixture_graph_db import LADDER_CASES

    rows = []
    for name, resolver_map in LADDER_CASES:
        entity, tier, suggestions = mod._resolve_ladder(name, resolver_map)
        rows.append(
            f"{name!r} map={sorted(resolver_map.items())} => "
            f"({entity!r}, {tier!r}, {suggestions!r})"
        )
    return "\n".join(rows)


def _render_theme_membership(mod: ModuleType) -> str:
    """Fixed Companies note tree; stems as company names (map=None)."""
    import tempfile
    from helpers.graph.fixture_graph_db import THEME_NOTES, build_note_tree

    with tempfile.TemporaryDirectory(prefix="parity-themes-") as tmp:
        root = build_note_tree(Path(tmp), THEME_NOTES) / "Companies"
        got = sorted(
            (company, theme, tuple(aliases))
            for company, theme, aliases in mod.extract_theme_membership(root=root)
        )
    return "\n".join(repr(row) for row in got)


def _render_extract_citations(mod: ModuleType) -> str:
    """Fixed derived-note vault; the vault must be NAMED findata (join keys)."""
    import tempfile
    from helpers.graph.fixture_graph_db import (
        CITED_EDITION_STEMS,
        CITED_PATH_TO_NAME,
        CITED_VAULT_NOTES,
        build_note_tree,
    )

    with tempfile.TemporaryDirectory(prefix="parity-cited-") as tmp:
        vault = build_note_tree(Path(tmp) / "findata", CITED_VAULT_NOTES)
        citations, stats = mod.extract_citations(
            vault, CITED_PATH_TO_NAME, set(CITED_EDITION_STEMS)
        )
    rows = [repr(c) for c in sorted(citations)]
    rows.append(f"stats={sorted(stats.items())}")
    return "\n".join(rows)


def _render_l1b_compute(mod: ModuleType) -> str:
    """Fixture graph DB through compute(); sorted scores + stable info dump."""
    import json
    import tempfile
    from helpers.graph.fixture_graph_db import build_graph_fixture_db

    with tempfile.TemporaryDirectory(prefix="parity-l1b-") as tmp:
        db = build_graph_fixture_db(Path(tmp) / "fixture.db")
        scores, info = mod.compute(db_path=str(db), jobs=1)
    rows = [f"{n}={s:.9f}" for n, s in sorted(scores.items())]
    rows.append("info=" + json.dumps(info, sort_keys=True, default=str))
    return "\n".join(rows)


def _render_near_duplicate_notes(mod: ModuleType) -> str:
    """The seeded fixture DB through the full parameter matrix.

    The fixture (helpers/graph/fixture_note_embeddings.py) seeds a tie
    lattice (four pairs bit-identical at 0.96, two at 1.0, four more at
    0.9899…), a multi-section path, a zero vector, and both doc types, so
    the render pins the `(-sim, path_a, path_b)` ordering, the canonical
    string orientation, the mean collapse, and the zero-norm guard (at
    min_sim=0.0 the zero-vector pairs are present at sim 0.0 — a dropped
    guard turns them NaN and they vanish). The 4x prune is deliberately
    NOT in this surface: the final sort+truncate makes the return value
    invariant to buffer size (the function's own docstring records this),
    so the prune is witnessed by the stats-seam tests in
    tests/test_note_embeddings.py, never by parity.
    """
    from helpers.graph.fixture_note_embeddings import near_dup_con

    rows: list[str] = []
    con = near_dup_con()
    try:
        for doc_type in ("company", "newsletter"):
            for min_sim in (0.9, 0.4, 0.0):
                for limit in (0, 1, 2, 100):
                    got = mod.near_duplicate_notes(
                        con, min_sim=min_sim, doc_type=doc_type, limit=limit
                    )
                    rows.append(f"doc_type={doc_type} min_sim={min_sim} limit={limit}")
                    for pair in got:
                        rows.append(f"  {pair!r}")
    finally:
        con.close()
    return "\n".join(rows)


REGISTRY: dict[str, Fixture] = {
    "bfs_path": Fixture(
        name="bfs_path",
        relpath="helpers/graph/csr.py",
        render=_render_bfs_path,
        note="level-BFS over CSR; no DB, so it needs no fixture database",
    ),
    # c901 S2 splits (#306), registered by ocr_remediation S4/F7 so their
    # one-shot parity proofs are re-runnable. Fixture inputs live in
    # helpers/graph/fixture_graph_db.py. print_stats is NOT registered: it
    # opens the live production DB through helpers.core.db.connect() and the
    # census/hygiene helpers — fixture-rendering it needs a db path threaded
    # through that plumbing (deferred c901 D1 owner).
    "extract_relations": Fixture(
        name="extract_relations",
        relpath="helpers/graph/extract_relations.py",
        render=_render_extract_relations,
        note="full pipeline over fixed newsletter blocks incl. zero-resolved list chunks",
    ),
    "_resolve_ladder": Fixture(
        name="_resolve_ladder",
        relpath="helpers/graph/derive_insights.py",
        render=_render_resolve_ladder,
        note="S2 tier ladder over exact/strip/qualifier/fuzzy/miss cases",
    ),
    "extract_theme_membership": Fixture(
        name="extract_theme_membership",
        relpath="helpers/graph/derive_themes.py",
        render=_render_theme_membership,
        note="theme scan over a fixed Companies note tree (stem-name convenience)",
    ),
    "extract_citations": Fixture(
        name="extract_citations",
        relpath="helpers/graph/derive_cited_in.py",
        render=_render_extract_citations,
        note="citations over a fixed vault incl. PDF-skip, unknown-id and unmapped-entity paths",
    ),
    "l1_betweenness_compute": Fixture(
        name="l1_betweenness_compute",
        relpath="helpers/graph/l1_betweenness.py",
        render=_render_l1b_compute,
        note="folded betweenness over a fixture graph DB (noise/self-loop rows included)",
    ),
    "near_duplicate_notes": Fixture(
        name="near_duplicate_notes",
        relpath="helpers/graph/query.py",
        render=_render_near_duplicate_notes,
        note="GEMM near-dup pipeline over a seeded tie lattice (c901_d1_split_near_duplicate_notes S1); prune lives in the stats-seam tests, not here",
    ),
    # Canaries: never part of a default run, but registered so the test suite
    # can drive both verdicts through the real subprocess path. A harness that
    # has only ever printed PASS is indistinguishable from no harness.
    "_canary_hash_order": Fixture(
        name="_canary_hash_order",
        relpath="helpers/graph/csr.py",
        render=lambda mod: "\n".join(sorted("abcdefghijkl", key=hash)),
        canary=True,
        note="render varies with PYTHONHASHSEED; proves DETERMINISM fires",
    ),
    "_canary_divergence": Fixture(
        name="_canary_divergence",
        relpath="helpers/graph/csr.py",
        render=_render_bfs_path,
        canary=True,
        inject_divergence="TAMPERED-WORKING-TREE",
        note="forces live-side divergence; proves PARITY can go red",
    ),
}


# --- worker: render one side, one seed ------------------------------------ #


def _load_baseline(
    ref: str, relpath: str, canonical: str, restore_after: bool = False
) -> ModuleType:
    """Exec ``git show <ref>:<relpath>`` as ``canonical`` and return it.

    The module object is handed straight to the fixture's render, so the
    baseline never has to be importable by name -- and its own intra-package
    imports still resolve against the working tree, which is exactly what we
    want: only the one file under test differs.
    """
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git not on PATH; parity needs a baseline to diff against")
    proc = subprocess.run(  # noqa: S603  # resolved absolute path, fixed argv, no shell
        [git, "show", f"{ref}:{relpath}"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"cannot read baseline {ref}:{relpath}: {proc.stderr.strip()}")
    spec = importlib.util.spec_from_loader(canonical, loader=None)
    if spec is None:
        raise RuntimeError(f"cannot build an import spec for {canonical}")
    module = importlib.util.module_from_spec(spec)
    # __file__ must match the live module's, or a fixture that reads it (or
    # opens something relative to it) sees a spurious difference.
    module.__file__ = str(REPO_ROOT / relpath)
    # Register before exec: py3.14 dataclass processing resolves string
    # annotations via sys.modules[cls.__module__] and gets None back (then
    # crashes) when the exec'd module was never registered.
    prev = sys.modules.get(canonical)
    sys.modules[canonical] = module
    try:
        exec(compile(proc.stdout, module.__file__, "exec"), module.__dict__)  # noqa: S102
    except BaseException:
        # A half-initialized module must not stay registered under the REAL
        # import name — later imports would resolve to it and die confusingly.
        if prev is None:
            sys.modules.pop(canonical, None)
        else:
            sys.modules[canonical] = prev
        raise
    # In-process callers (tests) pass restore_after=True: the exec'd baseline
    # must not outlive the call under the REAL import name, or every later
    # import of the live module silently resolves to HEAD's code. The worker
    # subprocess keeps the registration (lazy self-imports during render need
    # it; the process exits after render anyway).
    if restore_after:
        if prev is None:
            sys.modules.pop(canonical, None)
        else:
            sys.modules[canonical] = prev
    return module


def _worker(fixture: Fixture, side: str, ref: str) -> int:
    canonical = fixture.relpath.removesuffix(".py").replace("/", ".")
    if side == "live":
        module = importlib.import_module(canonical)
    else:
        module = _load_baseline(ref, fixture.relpath, canonical)
    sys.stdout.write(fixture.render(module))
    if side == "live" and fixture.inject_divergence:
        sys.stdout.write(f"\n{fixture.inject_divergence}\n")
    return 0


# --- driver --------------------------------------------------------------- #


def _run_side(fixture: Fixture, side: str, ref: str, seed: int) -> str:
    """Render one side in a fresh subprocess with ``seed`` pinned."""
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(seed)  # read at interpreter startup
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(  # noqa: S603  # sys.executable is absolute, fixed argv, no shell
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            fixture.name,
            "--side",
            side,
            "--ref",
            ref,
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{fixture.name}/{side}/seed={seed} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def _first_diff(a: str, b: str) -> str:
    """First differing line, for a diagnosable failure message."""
    a_lines, b_lines = a.splitlines(), b.splitlines()
    # F10: content identical modulo the final newline must be reported as
    # exactly that — the generic length message would claim "N vs N" lines.
    if a_lines == b_lines and a != b and a.rstrip("\n") == b.rstrip("\n"):
        return (
            "differs only in the trailing newline "
            f"(baseline ends with newline: {a.endswith(chr(10))}, "
            f"working: {b.endswith(chr(10))})"
        )
    for i, (la, lb) in enumerate(zip(a_lines, b_lines), start=1):
        if la != lb:
            return f"line {i}:\n    baseline: {la}\n    working:  {lb}"
    longer, side = (a_lines, "baseline") if len(a_lines) > len(b_lines) else (b_lines, "working")
    extra = ", ".join(longer[min(len(a_lines), len(b_lines)) :][:3])
    return (
        f"common prefix identical; length differs "
        f"(baseline {len(a_lines)} lines, working {len(b_lines)}); "
        f"extra on the {side} side: {extra}"
    )


def compare(fixture: Fixture, ref: str, seeds: tuple[int, ...]) -> tuple[bool, list[str]]:
    """Return (parity_ok, report lines) for one fixture."""
    report: list[str] = []
    ok = True
    # Render each (side, seed) exactly once (F11 — the parity and
    # determinism loops used to run every render twice) and reuse.
    renders = {
        (side, seed): _run_side(fixture, side, ref, seed)
        for side in ("baseline", "live")
        for seed in seeds
    }
    for seed in seeds:
        base, live = renders[("baseline", seed)], renders[("live", seed)]
        if base == live:
            report.append(
                f"  PARITY ok       seed={seed} ({len(live.splitlines())} rows identical)"
            )
            continue
        ok = False
        report.append(f"  PARITY MISMATCH seed={seed}")
        report.append(f"    {_first_diff(base, live)}")
    # F14: judge each seed against a FIXED baseline seed (the first of the
    # ordered tuple). The old condition never referenced the outer seed, so
    # one differing seed reported every seed as unstable.
    anchor = seeds[0]
    for side in ("baseline", "live"):
        side_renders = {seed: renders[(side, seed)] for seed in seeds}
        unstable = {s: r for s, r in side_renders.items() if r != side_renders[anchor]}
        if unstable:
            report.append(
                f"  DETERMINISM {side} output varies with PYTHONHASHSEED "
                f"(seeds {sorted(unstable)} differ from seed {anchor}) "
                "-- pre-existing, see chain_tally_determinism"
            )
    return ok, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("fixtures", nargs="*", help="fixture names (default: all)")
    parser.add_argument("--list", action="store_true", help="list registered fixtures")
    parser.add_argument(
        "--ref", default=DEFAULT_REF, help=f"git ref to diff against (default {DEFAULT_REF})"
    )
    parser.add_argument("--seeds", default=",".join(str(s) for s in SEEDS))
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on divergence (default: warn only -- see the note in main())",
    )
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--side", choices=("live", "baseline"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.worker:
        return _worker(REGISTRY[args.worker], args.side, args.ref)

    if args.list:
        for name, fix in REGISTRY.items():
            kind = "canary" if fix.canary else "fixture"
            print(f"{name:24s} {fix.relpath:32s} [{kind}] {fix.note}")
        return 0

    names = args.fixtures or [n for n, f in REGISTRY.items() if not f.canary]
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        print(f"unknown fixture(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"registered: {', '.join(REGISTRY) or '(none)'}", file=sys.stderr)
        return 2
    # F9: a blank or malformed --seeds used to parse to an empty tuple and
    # print a vacuous "N/N byte-identical" with exit 0. Never a traceback,
    # never a vacuous run: argparse-error instead.
    raw_seeds = [s.strip() for s in args.seeds.split(",")]
    if not raw_seeds or not all(s.isdecimal() for s in raw_seeds):
        parser.error(
            f"--seeds must be a comma-separated list of non-negative integers "
            f"(got {args.seeds!r}; e.g. 0,1,7)"
        )
    seeds = tuple(int(s) for s in raw_seeds)

    print(f"parity vs {args.ref} | seeds {seeds}")
    failures = 0
    for name in names:
        ok, report = compare(REGISTRY[name], args.ref, seeds)
        print(f"{name}: {'PASS' if ok else 'DIVERGED'}")
        print("\n".join(report))
        failures += 0 if ok else 1
    total = len(names)
    print(f"\n{total - failures}/{total} fixture(s) byte-identical to {args.ref}")
    if not failures:
        return 0
    # Warn, do not assert. Parity is only meaningful against a chosen ref and
    # on a dirty tree, so a divergence is a prompt to look, not a build
    # failure -- a false positive here would only teach people to skip the
    # harness. Pass --strict to gate on it.
    print(
        f"\nWARNING: {failures} of {total} fixture(s) diverged from {args.ref}.\n"
        "  A refactor that changes output is a bug: revert it, do not adjust\n"
        "  the fixture to match. If the behaviour change is the intent, say so\n"
        "  in the commit and re-baseline with --ref.",
        file=sys.stderr,
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
