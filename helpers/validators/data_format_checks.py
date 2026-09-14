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


def _iter_py() -> list[Path]:
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


def scan_zstd_violations() -> dict[str, str]:  # noqa: C901 — one walk, three node kinds (call/const/fstr)
    """{site_key: description} for parquet writes missing zstd."""
    out: dict[str, str] = {}
    for path in _iter_py():
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


def scan_arrow_violations() -> dict[str, str]:
    """{site_key: description} for non-Arrow producers in data-lane modules."""
    out: dict[str, str] = {}
    for path in _iter_py():
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


def check_data_format() -> tuple[list[str], list[str]]:
    """static_checks entry: (fatal, advisory)."""
    fatal: list[str] = []
    advisory: list[str] = []
    z = scan_zstd_violations()
    a = scan_arrow_violations()
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
    # Baseline hygiene: entries whose site no longer offends must be removed.
    for key in _ZSTD_BASELINE:
        if key not in z:
            fatal.append(f"stale _ZSTD_BASELINE entry: {key} (site is clean — remove)")
    for key in _ARROW_BASELINE:
        if key not in a:
            fatal.append(f"stale _ARROW_BASELINE entry: {key} (site is clean — remove)")
    return fatal, advisory


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
