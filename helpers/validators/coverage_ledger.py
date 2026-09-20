#!/usr/bin/env python3
"""Coverage-ledger validator — the cumulative-coverage gate (S3 of the
security-coverage-expansion arc, completed.md #247b, archived at
doc/improvements/archive/security/post_review_api_reaudit.md).

The security evaluation (doc/local/security/security_evaluation.md) is a
SNAPSHOT: 13 routes shipped after it closed with no review, and a later
inventory nearly mis-recorded /api/graph/refresh as removed because a
literal-match scan drops routes whose decorator carries a `methods=`
argument. This validator turns the coverage claim into a deterministic,
machine-checked structure instead of prose.

Contract:
  completeness — every route in app.py must have >=1 ledger row, else the
    route is UNREVIEWED (exit 1). This is the check that catches a new
    39th route the day it lands, not at the next manual recount.
  freshness   — each row carries a fingerprint of its handler's source at
    review time. A mismatch is STALE (exit 1 with --strict, warning
    otherwise): the handler changed after the recorded review, so the
    coverage no longer applies and the surface must be re-reviewed.
    Scope note: the fingerprint hashes the handler BODY (def line down),
    not the decorator — a changed route path or methods list does NOT
    trip STALE. Route-shape changes are caught by consistency only when
    the path string itself changes or the route disappears.
  consistency — a ledger row naming a route that no longer exists in
    app.py is ORPHANED (exit 1): the route was removed or renamed and the
    ledger must be corrected.

Row shape (one row per surface x attack class):
  {surface, kind, attack_class, reviewed, method, verdict, fingerprint}

`kind` is "route" (completeness- and consistency-checked against app.py),
"ingestion" (operator-triggered, not HTTP-reachable), or "shared"
(cross-cutting HTTP-reachable surfaces spanning many routes, e.g. the
shared graph connection). Only "route" rows are matched against app.py.
READ-ONLY in check mode; `seed` recomputes fingerprints only.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_APP = REPO / "app.py"
DEFAULT_LEDGER = REPO / "doc/local/security/coverage-ledger.json"


def route_inventory(app_path: Path) -> dict[str, dict]:
    """Parse app.py for @app.route decorators -> {path: meta}.

    Uses ast, so routes with a `methods=` argument (the ones a literal
    `@app.route("<literal>")` grep silently drops) are counted. The route
    path is the FIRST string literal of the decorator call.
    """
    text = app_path.read_text()
    tree = ast.parse(text)
    out: dict[str, dict] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = dec.func
            name = getattr(getattr(fn, "attr", None), "id", None) or getattr(fn, "attr", None)
            if getattr(fn, "value", None) is None or name != "route":
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            path = dec.args[0].value
            if not isinstance(path, str):
                continue
            methods = sorted(
                str(el.value)
                for kw in dec.keywords
                if kw.arg == "methods" and isinstance(kw.value, ast.List)
                for el in kw.value.elts
                if isinstance(el, ast.Constant)
            ) or ["GET"]
            seg = ast.get_source_segment(text, node)
            # Same truthiness as ``dec.lineno and seg or ""`` but type-stable:
            # hash the handler's source when the decorator has a real line,
            # else a constant empty string.
            snippet = (seg or "") if dec.lineno else ""
            fp = hashlib.sha256(snippet.encode()).hexdigest()[:16]
            out[path] = {
                "handler": node.name,
                "line": dec.lineno,
                "methods": methods,
                "fingerprint": fp,
            }
    return out


def load_ledger(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    if not isinstance(data, list):
        sys.exit(f"ledger must be a JSON array: {path}")
    return data


def check(app_path: Path, ledger_path: Path, *, strict: bool) -> int:  # noqa: C901
    routes = route_inventory(app_path)
    rows = load_ledger(ledger_path)
    by_surface: dict[str, list[dict]] = {}
    for r in rows:
        by_surface.setdefault(r["surface"], []).append(r)

    errors: list[str] = []
    warns: list[str] = []

    # 1. completeness: every route has >=1 row
    for path in sorted(routes):
        if path not in by_surface:
            errors.append(
                f"UNREVIEWED {path} (handler {routes[path]['handler']} "
                f"app.py:{routes[path]['line']}) — no ledger row; "
                f"review it or seed a row"
            )

    # 2. freshness: fingerprints match the reviewed handler source
    for surface, rs in sorted(by_surface.items()):
        live = routes.get(surface)
        for r in rs:
            if r.get("kind") != "route":
                continue
            if live is None:
                errors.append(
                    f"ORPHANED row for {surface} — not in app.py "
                    "(removed or renamed); correct the ledger"
                )
                continue
            if r.get("fingerprint") != live["fingerprint"]:
                msg = (
                    f"STALE {surface} / {r.get('attack_class')} — handler "
                    f"changed since review {r.get('reviewed')}; re-review"
                )
                (errors if strict else warns).append(msg)

    for w in warns:
        print(f"warn: {w}")
    for e in errors:
        print(f"FAIL: {e}")
    n = len(routes)
    covered = sum(1 for p in routes if p in by_surface)
    print(f"coverage: {covered}/{n} routes have a ledger row")
    if errors:
        return 1
    print("ok: ledger complete, no orphaned rows")
    return 0


def seed(app_path: Path, ledger_path: Path) -> int:
    """Recompute fingerprints for existing rows and write them back.

    Run after a deliberate re-review whose verdict did not change but
    whose handler source did (formatting, refactor). Verdicts are curated
    by hand — this never invents coverage.
    """
    routes = route_inventory(app_path)
    rows = load_ledger(ledger_path)
    updated = 0
    for r in rows:
        live = routes.get(r["surface"])
        if live and r.get("fingerprint") != live["fingerprint"]:
            r["fingerprint"] = live["fingerprint"]
            updated += 1
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    print(f"seed: updated {updated} fingerprints; {len(rows)} rows")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["check", "seed"])
    ap.add_argument("--app", type=Path, default=DEFAULT_APP)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--strict", action="store_true", help="stale rows fail")
    a = ap.parse_args(argv)
    if a.mode == "check":
        return check(a.app, a.ledger, strict=a.strict)
    return seed(a.app, a.ledger)


if __name__ == "__main__":
    raise SystemExit(main())
