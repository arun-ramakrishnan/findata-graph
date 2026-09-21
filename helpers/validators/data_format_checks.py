#!/usr/bin/env python3
"""S19 — data-format enforcement guards (architecture.md §10).

Prose standards drift; checks do not. Two guards over the helper tree:

**(a) At-rest — parquet(zstd) codec check.** Every parquet write in
``helpers/**`` + ``app.py`` must pin zstd: ``pq.write_table(...)`` /
``.to_parquet(...)`` call sites need ``compression="zstd"``; DuckDB
``COPY ... TO '*.parquet'`` SQL needs ``COMPRESSION zstd``. Existing
offenders live in ``_ZSTD_BASELINE`` with a reason and exit slice —
baselines only ever shrink.

**(b) In-flight — Arrow between components.** ``load_*`` / ``read_*``
producers in the designated data-lane modules must return Arrow
(annotation carries ``pa.Table``). Custom dict/list/tuple producers are
violations unless baselined in ``_ARROW_BASELINE`` with reason + exit.
HGX ``to_pylist()`` boundaries are exempt BY DESIGN (engine wants
Python objects — never baselined); engine-internal reads stay out of
scope.

Baseline hygiene is itself checked: an entry whose site no longer
offends fails loudly ("remove entry") so the ledger cannot rot.

Wired into ``make static_checks`` via static_checks.py.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = [PROJECT_ROOT / "helpers", PROJECT_ROOT / "app.py"]

DATA_LANE_MODULES = {
    "helpers/graph/hyper_arrow.py",
    "helpers/graph/hyper_communities.py",
    "helpers/graph/hyper_centralities.py",
    "helpers/graph/hyper_hif.py",
}

# At-rest straggler ledger — {relpath:lineno-literal : reason}.
_ZSTD_BASELINE: dict[str, str] = {}

# In-flight straggler ledger — {relpath:func : (reason, exit slice)}.
# EMPTY since 2026-09-14: both founding entries retired — the communities
# and centralities CLIs consume load_incidence_arrow directly and derive
# their working shapes via incidence_dict / weights_from_arrow (the
# sanctioned Arrow->dict boundaries in hyper_arrow.py).
_ARROW_BASELINE: dict[str, tuple[str, str]] = {}


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(PROJECT_ROOT))
    except ValueError:  # synthetic trees (tests) live outside the repo
        return str(p)


def _iter_py(scope: set[Path] | None = None) -> list[Path]:
    """Files to scan: scope members when gated (no walk), else the walk.

    Scope-driven iteration (gate_latency_followups Slice C): dirty files
    under the scan roots only, live-checked (TOCTOU-safe).
    """
    if scope is not None:
        out = []
        for p in scope:
            if p.suffix != ".py" or not p.is_file():
                continue
            if "__pycache__" in p.parts:
                continue
            try:
                rel = p.relative_to(PROJECT_ROOT).as_posix()
            except ValueError:
                continue
            if p == PROJECT_ROOT / "app.py" or rel.startswith("helpers/"):
                out.append(p)
        return sorted(out)
    files = []
    for root in SCAN_ROOTS:
        if root.is_file():
            files.append(root)
        else:
            files.extend(sorted(root.rglob("*.py")))
    return [f for f in files if "__pycache__" not in f.parts]


def _has_zstd_kw(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "compression":
            v = kw.value
            if (
                isinstance(v, ast.Constant)
                and isinstance(v.value, str)
                and v.value.lower() == "zstd"
            ):
                return True
    return False


def _has_zstd_kw(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "compression":
            v = kw.value
            if (
                isinstance(v, ast.Constant)
                and isinstance(v.value, str)
                and v.value.lower() == "zstd"
            ):
                return True
    return False


def _joined_str_text(node: ast.JoinedStr) -> str:
    """Reconstruct an f-string's literal parts (values are unknowable)."""
    return "".join(
        v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
    )


def _docstring_spans(tree: ast.AST) -> set[int]:
    """Line numbers of docstring Constants (module + every def/class)."""
    spans: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                spans.add(body[0].value.lineno)
    return spans


def scan_zstd_violations(scope: set[Path] | None = None) -> dict[str, str]:  # noqa: C901 — one walk, three node kinds (call/const/fstr)
    """{site_key: description} for parquet writes missing zstd."""
    out: dict[str, str] = {}
    for path in _iter_py(scope):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = _rel(path)
        doc = _docstring_spans(tree)
        parents: dict[ast.AST, ast.AST] = {}
        for p in ast.walk(tree):
            for ch in ast.iter_child_nodes(p):
                parents[ch] = p

        def fn_of(n: ast.AST) -> str:
            while n in parents:
                n = parents[n]
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return n.name
            return "<module>"

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                if not isinstance(node.value, str) or node.lineno in doc:
                    continue  # non-strings and docstrings are prose, not SQL
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = _joined_str_text(node)
            else:
                if isinstance(node, ast.Call):
                    f = node.func
                    name = (
                        f.attr
                        if isinstance(f, ast.Attribute)
                        else (f.id if isinstance(f, ast.Name) else "")
                    )
                    if name in ("write_table", "to_parquet") and not _has_zstd_kw(node):
                        out.setdefault(
                            f"{rel}:{fn_of(node)}",
                            "parquet write without compression='zstd'",
                        )
                continue
            s = text.upper()
            if "COPY" in s and "PARQUET" in s and "ZSTD" not in s:
                out.setdefault(
                    f"{rel}:{fn_of(node)}",
                    "DuckDB COPY ... TO '*.parquet' without COMPRESSION ZSTD",
                )
    return out


def scan_arrow_violations(scope: set[Path] | None = None) -> dict[str, str]:
    """{site_key: description} for non-Arrow producers in data-lane modules."""
    out: dict[str, str] = {}
    for path in _iter_py(scope):
        rel = _rel(path)
        if rel not in DATA_LANE_MODULES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:  # module-level defs only
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not (node.name.startswith("load_") or node.name.startswith("read_")):
                continue
            ret = node.returns
            ann = ast.unparse(ret) if ret is not None else ""
            if "pa.Table" in ann or "Table" in ann.split("[")[-1]:
                continue  # Arrow-valued (incl. dict[str, pa.Table])
            body_src = ast.unparse(node)
            is_engine_boundary = "to_pylist" in body_src and "Hypergraph" in body_src
            if is_engine_boundary:
                continue  # HGX construction boundary (sanctioned, never baselined)
            out[f"{rel}:{node.name}"] = f"non-Arrow return '{ann or 'None'}' in data-lane producer"
    return out


def _scoped_scan_rels(scope: set[Path]) -> set[str]:
    """Rels scanned this run: scope members under the scan roots.

    Full runs (scope None) scan everything — represented as None by the
    caller, meaning every baseline key is checkable.
    """
    scanned = set()
    for p in scope:
        if p.suffix != ".py" or not p.is_file():
            continue
        try:
            rel = p.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            continue
        if p == PROJECT_ROOT / "app.py" or rel.startswith("helpers/"):
            scanned.add(rel)
    return scanned


def _check_baseline_hygiene(
    z: dict[str, str],
    a: dict[str, str],
    scanned: set[str] | None,
    fatal: list[str],
) -> None:
    """Baseline hygiene: entries whose site no longer offends must be removed.

    A key is checkable only when its file was scanned this run (full runs
    scan everything; scoped runs scan the scope) — an unscanned file
    can't prove staleness, so its keys defer to full runs.
    """
    for key in _ZSTD_BASELINE:
        if key not in z and _key_scanned(key, scanned):
            fatal.append(f"stale _ZSTD_BASELINE entry: {key} (site is clean — remove)")
    for key in _ARROW_BASELINE:
        if key not in a and _key_scanned(key, scanned):
            fatal.append(f"stale _ARROW_BASELINE entry: {key} (site is clean — remove)")


def check_data_format(scope: set[Path] | None = None) -> tuple[list[str], list[str]]:
    """static_checks entry: (fatal, advisory).

    A scope restricts the scans to dirty files (dirty-gating); None =
    full. Baseline hygiene is enforced for entries whose file was
    scanned this run — full runs check every entry; scoped runs check
    only in-scope files (a key whose file wasn't scanned can't be
    proven stale, so it defers to full runs).
    """
    fatal: list[str] = []
    advisory: list[str] = []
    z = scan_zstd_violations(scope)
    a = scan_arrow_violations(scope)
    scanned = None if scope is None else _scoped_scan_rels(scope)
    for key, desc in z.items():
        if key in _ZSTD_BASELINE:
            advisory.append(f"zstd baseline: {key} — {_ZSTD_BASELINE[key]}")
        else:
            fatal.append(f"{key}: {desc}")
    for key, desc in a.items():
        if key in _ARROW_BASELINE:
            reason, exit_ = _ARROW_BASELINE[key]
            advisory.append(f"arrow baseline: {key} — {reason} ({exit_})")
        else:
            fatal.append(f"{key}: {desc}")
    _check_baseline_hygiene(z, a, scanned, fatal)
    return fatal, advisory


def _key_scanned(key: str, scanned: set[str] | None) -> bool:
    """True when a baseline key's file was scanned this run.

    Keys are `{rel}:{site}` — the file part must exactly equal a scanned
    rel (no prefix matching: `helpers/a.py` must never match
    `helpers/a.py2`).
    """
    if scanned is None:
        return True
    return key.rsplit(":", 1)[0] in scanned


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    fatal, advisory = check_data_format()
    for f in fatal:
        print(f"✗ {f}")
    for a in advisory:
        print(f"~ {a}")
    print(f"data_format_checks: {len(fatal)} fatal, {len(advisory)} baselined")
    return 1 if fatal else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
