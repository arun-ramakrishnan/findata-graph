#!/usr/bin/env python3
"""Scope-family inventories for the security lane (repo-wide pass, Q4).

The coverage ledger's scope vocabulary names SEVEN families, but only
`app` (a machine inventory parsed from app.py) and `extractors` ever had
anything at row time. Execution-arc audits DID happen in the other
families — they were simply never converged into the lane. This module
is where each of the five remaining families and its security surface
areas are enumerated and marked with the surface's neighborhood, the
executed audit arc when one exists, and the disposition of what the
audit left closed versus unresolved.

Storage -- the inventory itself is gitignored (`doc/local/security/`
inventories.json): it carries posture detail. The TRACKING side is the
coverage ledger (verdict tokens). This module is its editable authority.

Usage:
    security_scopes.py inventory                  # print all scopes
    security_scopes.py inventory --scope desktop  # one scope
    security_scopes.py seed-inventories           # write the
        doc/local/security/inventories.json skeleton the CLI prints
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = REPO / "doc/security/coverage-ledger.json"

REPO = Path(__file__).resolve().parents[2]
INVENTORY_PATH = REPO / "doc" / "local" / "security" / "inventories.json"

# Per scope: the areas that make up the family and (when one exists) the
# run/arc that already audited it, so a future re-audit has a starting
# point instead of a blank page. Surfaces ARE the module/file family --
# NOT enforcement claims: a surface's disposition still requires rows in
# the coverage ledger, which this module only *informs*.

_SCOPE_AREAS: dict[str, tuple[dict[str, str], ...]] = {
    # Flask routes -- the ONLY machine inventory (helpers/validators/
    # coverage_ledger.py route_inventory()). NOT listed here because the
    # route walk IS its inventory.
    "app": (),
    # Ingestion boundary  0x2192 the graph. Audited: pdf parse surfaces,
    # image-fetch, derive regexes (ledger rows 44-49 family).
    "extractors": (
        {
            # The area strings ARE the ledger's surface names for this
            # family: a surface that appears under two names is a pair
            # that reads as uncovered on one side and orphaned on the other.
            "path": "pdf parse (pymupdf.open sites, helpers/pdf/*)",
            "arc": "SEC-6 2026-09-18 (unbounded-read finding REMEDIATED 2026-10-20)",
        },
        {
            "path": "derive_insights._PCT_RE/_BPS_RE + derive_events._PCT_RE",
            "arc": "SEC-6 classification audit; parser-input sweep OPEN",
        },
        {
            "path": "capture_newsletter_images.fetch",
            "arc": "A6-verified 2026-10-20; bounded read recorded",
        },
    ),
    # Graph pipeline  0x2192 DuckDB/igraph workers. Audited: f-string SQL
    # family proved-in via S608-parameterization comments.
    "graph": (
        {
            "path": "helpers/graph/derive_insights.py:1586 :1602 :2137",
            "arc": "S608-proved parameterized projections (schema-constant identifiers)",
        },
        {
            "path": "helpers/graph/name_dedup.py:373 (DELETE ... IN)",
            "arc": "S608-proved; column tuple closed literals",
        },
        {
            "path": "helpers/graph/*.py (non-SQL derive/backfill/CSR)",
            "arc": "never audit-targeted; repo-local trusted inputs",
        },
    ),
    # Operator tooling. Audited: snapshot-restore SQL injection arc (5838
    # confirmed), machine-local stores convention, sandbox promotion
    # explicitly excluded by design (scan procedure, non-goal).
    "harness": (
        {
            "path": "helpers/maintenance/snapshot_db.py:restore_duckdb_from_parquet",
            "arc": "SQL injection HIGH -- manifest gate executed 2026-10-01 (completed.md #333)",
        },
        {
            "path": "helpers/core/*.py (db, vec_search, embed_*) f-string SQL",
            "arc": "S608-proved family: `?`-binds / schema-constant identifiers",
        },
        {
            "path": "helpers/maintenance/* (non-SQL rewriters)",
            "arc": "S608-proved where interpolating; else no attacker-controlled input",
        },
        {
            "path": "machine-local sqlite engines (bili tiers, convo_search)",
            "arc": "gitignored + out of scan scope by procedure",
        },
    ),
    # Tauri shell + its Vue. Audited: Addendum 7  0xa7A family-audit
    # (2026-10-01, completed.md #332) plus deployed hardening arc.
    "desktop": (
        {
            "path": "desktop/src-tauri (CSP, IPC, capabilities/default.json)",
            "arc": "family-audited 2026-10-01 -- zero confirmed findings, 3 hardening notes",
        },
        {
            "path": "desktop/src-vue (markdown-it html:false; snippetHtml filter)",
            "arc": "sinks verified (same arc); NotePanel webview renders OCR text",
        },
    ),
    # Browser-served bundles. Audited: SEC-4 innerHTML/escapeHtml audit
    # (scan procedure class-table row) + vendored purify.
    "frontend": (
        {"path": "frontend/src (TS app)", "arc": "SEC-4 full innerHTML/escapeHtml audit"},
        {
            "path": "static/ (bundles; static/vendor/purify.min.js vendored)",
            "arc": "SEC-4; vendored sanitizer shipped at 3.x",
        },
        {
            "path": "templates/ (2 server-rendered pages)",
            "arc": "covered by the same SEC-4 sweep (:63 procedure scan block)",
        },
    ),
    # Content at rest (2926 tracked findata files + snapshots). NOT a
    # processing surface  0x2192 leak/review discipline. Never targeted
    # by an arc; content scans are the operator's local procedure
    # (machine-local stores stay gitignored out of scan scope).
    "data": (
        {
            "path": "findata/** (markdown vault, 2926 files)",
            "arc": "content-scan discipline only (procedure \u00a7 scan-scope note); machine-local stores gitignored",
        },
        {
            "path": "snapshots/ (tracked parquet restore input)",
            "arc": "consumed only via the manifest-gated restore (harness entry)",
        },
    ),
}


def member_surfaces(scope: str) -> tuple[str, ...]:
    """The area strings (paths) that a scope's inventory enumerates."""
    return tuple(a["path"] for a in _SCOPE_AREAS.get(scope, ()))


def scope_skeleton() -> dict[str, list[dict]]:
    """Everything the inventory JSON needs, one entry per (scope, area)."""
    out: dict[str, list[dict]] = {}
    for scope, areas in _SCOPE_AREAS.items():
        out[scope] = [
            {
                "area": a["path"],
                "arc": a.get("arc", ""),
                "ledgered": False,
            }
            for a in areas
        ]
    return out


def load_inventory(path: Path | None = None) -> dict[str, list[dict]]:
    """Read doc/local/security/inventories.json, a loud gate on absence.

    The file is gitignored on purpose; the seed skeleton is committed to
    the operator's local setup separately (seed-inventories mode). This
    means a fresh checkout has a loud, honest gap -- never a silent
    empty inventory.
    """
    target = path or INVENTORY_PATH
    if not target.exists():
        sys.exit(
            f"security_scopes: inventory not found: {target}\n"
            "  seed it once with: security_scopes.py seed-inventories"
        )
    data = json.loads(target.read_text())
    if not isinstance(data, dict):
        sys.exit(f"security_scopes: inventory must be a JSON object: {target}")
    unknown = set(data) - set(_SCOPE_AREAS)
    if unknown:
        sys.exit(
            f"security_scopes: inventory names unknown scope(s) {sorted(unknown)}; "
            f"known: {sorted(_SCOPE_AREAS)}"
        )
    return data


def _load_ledger_rows(path: Path) -> list[dict]:
    """Read the tracked coverage ledger's rows (no import cycle)."""
    if not path.exists():
        sys.exit(f"security_scopes: ledger not found: {path}")
    data = json.loads(path.read_text())
    rows = data.get("rows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        sys.exit(f"security_scopes: ledger must hold a rows array: {path}")
    return [r for r in rows if isinstance(r, dict)]


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    ap = argparse.ArgumentParser(description="scope-family security inventories")
    ap.add_argument("mode", choices=["inventory", "seed-inventories", "sync"])
    ap.add_argument(
        "--scope",
        choices=sorted(_SCOPE_AREAS),
        help="restrict inventory print to one scope family",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=INVENTORY_PATH,
        help="seed-inventories target path (gitignored)",
    )
    a = ap.parse_args(argv)

    if a.mode == "seed-inventories":
        if a.out.exists():
            print(f"seed-inventories: {a.out} already exists - left untouched", file=sys.stderr)
            return 1
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(scope_skeleton(), indent=1) + "\n")
        print(f"seed-inventories: wrote {a.out}")
        return 0

    if a.mode == "sync":
        # The inventory's area strings ARE the ledger's surface names for
        # the non-app families. A drift between them is invisible in the
        # critic: a renamed area reads as an uncovered pair on one side
        # and an orphaned row on the other. `sync` reports the drift
        # instead of letting it settle.
        data = load_inventory(a.out)
        rows = _load_ledger_rows(DEFAULT_LEDGER)
        drift: list[str] = []
        for scope, entries in sorted(data.items()):
            if scope == "app":
                continue
            areas = {e.get("area") for e in entries}
            ledgered = {r.get("surface") for r in rows if (r.get("scope") or "app") == scope}
            for name in sorted(x for x in areas - ledgered if x is not None):
                drift.append(f"{scope}: inventory area with no ledger row: {name}")
            for name in sorted(x for x in ledgered - areas if x is not None):
                drift.append(f"{scope}: ledger row naming no inventory area: {name}")
        if drift:
            for line in drift:
                print(f"DRIFT {line}")
            print(f"sync: {len(drift)} drift(s) — the critic cannot see these")
            return 1
        print("sync: inventory areas and ledger surfaces agree")
        return 0

    data = load_inventory(a.out)
    scopes = [a.scope] if a.scope else sorted(data)
    for scope in scopes:
        entries = data.get(scope, [])
        print(f"## {scope}: {len(entries)} area(s)")
        for e in entries:
            mark = "x" if e.get("ledgered") else " "
            print(f"  [{mark}] {e.get('area')}")
            if e.get("arc"):
                print(f"      arc: {e['arc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
